"""Run only against an isolated PostgreSQL test database, never the campaign DB."""
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from app.main import app, processing_slot
from app.config import settings
from app.db import engine, SessionLocal
from app.models import Measurement, DemoMeasurement
from app.services.errors import MeasurementError


@unittest.skipUnless(os.environ.get('RUN_DB_TESTS')=='1' and engine.url.database.endswith('_test'), 'Requires isolated _test PostgreSQL database and RUN_DB_TESTS=1')
class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.old_dir=settings.image_dir;cls.old_store=settings.store_images
        settings.image_dir=cls.temp.name;settings.store_images=True
        cls.client=TestClient(app);cls.client.__enter__()
        picture=io.BytesIO();Image.new('RGB',(600,800),'white').save(picture,format='JPEG')
        cls.picture=picture.getvalue()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None,None,None)
        settings.image_dir=cls.old_dir;settings.store_images=cls.old_store
        cls.temp.cleanup()

    def setUp(self):
        self.person='MVPTEST-'+uuid.uuid4().hex
        self.result=dict(height_cm=170.,shoulder_cm=40.,waist_cm=85.,front_waist_width_cm=32.,side_waist_depth_cm=22.,confidence=.91,status='good',notes='Synthetic pipeline test, not real measurements.')

    def tearDown(self):
        with SessionLocal.begin() as db:
            db.execute(delete(Measurement).where(Measurement.external_id==self.person))
            db.execute(delete(DemoMeasurement).where(DemoMeasurement.external_id==self.person))
        for path in Path(self.temp.name).glob('*.jpg'):path.unlink()

    def post(self,person=None,filename='photo.jpg',data=None):
        image=self.picture if data is None else data
        return self.client.post('/api/measure',data={'person_id':self.person if person is None else person},files={'front_image':(filename,image,'image/jpeg'),'side_image':('side.jpg',image,'image/jpeg')})

    def test_startup_health_static_marker(self):
        self.assertEqual(self.client.get('/health').json()['database'],'connected')
        for path in ('/','/static/style.css','/static/app.js','/marker','/marker.svg','/marker.png','/docs'):
            self.assertEqual(self.client.get(path).status_code,200,path)
        self.assertIn('15 cm',self.client.get('/marker').text)

    def test_save_list_lookup_duplicate_and_safe_files(self):
        with patch('app.main.measure_person',return_value=self.result):
            response=self.post(filename='../../unsafe.jpg')
            self.assertEqual(response.status_code,200,response.text)
            value=response.json()
            self.assertTrue(value['success']);self.assertEqual(value['person_id'],self.person)
            self.assertEqual(self.post().status_code,409)
        rows=self.client.get('/api/measurements/'+self.person).json()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['height_cm'],170.)
        self.assertEqual(len(list(Path(self.temp.name).glob('*.jpg'))),2)
        for key in ('front_image_filename','side_image_filename'):
            filename=rows[0][key]
            self.assertNotIn('..',filename);self.assertTrue((Path(self.temp.name)/filename).is_file())
        self.assertTrue(any(row['person_id']==self.person for row in self.client.get('/api/measurements').json()))

    def test_invalid_uploads_and_missing_fields(self):
        self.assertEqual(self.client.post('/api/measure',data={'person_id':self.person}).status_code,422)
        self.assertEqual(self.post(person='   ').status_code,400)
        self.assertEqual(self.post(filename='photo.heic').status_code,415)
        self.assertEqual(self.post(data=b'broken').status_code,422)
        self.assertEqual(self.post().status_code,422)  # Real marker check on blank image.
        with SessionLocal() as db:self.assertIsNone(db.scalar(select(Measurement).where(Measurement.external_id==self.person)))
        self.assertEqual(list(Path(self.temp.name).glob('*.jpg')),[])

    def test_low_quality_not_saved(self):
        with patch('app.main.measure_person',side_effect=MeasurementError('Retake recommended')):
            response=self.post()
        self.assertEqual(response.status_code,422)
        self.assertIn('Retake',response.json()['error'])
        self.assertEqual(self.client.get('/api/measurements/'+self.person).status_code,404)

    def test_upload_size_and_busy(self):
        response=self.client.post('/api/measure',content=b'',headers={'Content-Length':'40000000'})
        self.assertEqual(response.status_code,413)
        self.assertEqual(self.post(data=b'x'*15_000_001).status_code,413)
        processing_slot.acquire()
        try:self.assertEqual(self.post().status_code,429)
        finally:processing_slot.release()

    def test_database_failure_cleans_saved_files(self):
        with patch('app.main.measure_person',return_value=self.result),patch('sqlalchemy.orm.Session.commit',side_effect=SQLAlchemyError('simulated failure')):
            response=self.post()
        self.assertEqual(response.status_code,503,response.text)
        self.assertEqual(list(Path(self.temp.name).glob('*.jpg')),[])

    def test_real_pose_model_no_person(self):
        from app.services.pose_service import detect_pose
        import numpy as np
        with self.assertRaisesRegex(MeasurementError,'No person'):
            detect_pose(np.full((600,600,3),255,np.uint8))

    def test_demo_without_photos_is_labelled_and_isolated(self):
        with patch('app.main.measure_person',side_effect=AssertionError('Demo must not run CV')):
            response=self.client.post('/api/demo/measure',data={'person_id':self.person})
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertEqual(data['mode'],'demo');self.assertEqual(data['status'],'demo')
        self.assertIn('not measurements',data['notes'])
        self.assertEqual(data['record']['mode'],'demo')
        self.assertEqual(self.client.get('/api/measurements/'+self.person).status_code,404)
        saved=self.client.get('/api/measurements/'+self.person+'?mode=demo').json()
        self.assertEqual(saved[0]['height_cm'],174.6)
        self.assertEqual(saved[0]['mode'],'demo')
        self.assertIsNone(saved[0]['front_image_filename'])
        self.assertEqual(self.client.post('/api/demo/measure',data={'person_id':self.person}).status_code,409)
        with patch('app.main.measure_person',return_value=self.result):
            self.assertEqual(self.post().status_code,200)  # Same ID in real mode is independent.

    def test_demo_accepts_markerless_photos_and_saves_images(self):
        with patch('app.main.measure_person',side_effect=AssertionError('No CV in demo')):
            response=self.client.post('/api/demo/measure',data={'person_id':self.person},files={
                'front_image':('front.jpg',self.picture,'image/jpeg'),
                'side_image':('side.jpg',self.picture,'image/jpeg')})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'demo')
        self.assertEqual(len(list(Path(self.temp.name).glob('*.jpg'))),2)

    def test_demo_still_rejects_corrupt_uploads(self):
        response=self.client.post('/api/demo/measure',data={'person_id':self.person},files={
            'front_image':('front.jpg',b'broken','image/jpeg')})
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.client.get('/api/measurements/'+self.person+'?mode=demo').status_code,404)

    def test_person_id_with_slash_can_be_found(self):
        from urllib.parse import quote
        self.person += '/section A'
        response = self.client.post('/api/demo/measure', data={'person_id': self.person})
        self.assertEqual(response.status_code, 200, response.text)
        lookup = self.client.get('/api/measurements/' + quote(self.person, safe='') + '?mode=demo')
        self.assertEqual(lookup.status_code, 200, lookup.text)
        self.assertEqual(lookup.json()[0]['person_id'], self.person)


if __name__=='__main__':unittest.main()
