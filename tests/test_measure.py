import io
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image
from app.services.aruco_service import detect_scale, marker_image, marker_svg
from app.services.errors import MeasurementError
from app.services.measurement_service import decode_image, analyze_view, measure_person, ellipse_circumference
from app.services.segmentation_service import torso_width, pose_height_bounds
from app.services.pose_service import detect_pose


def fixture(side=False):
    photo = np.full((1000, 800, 3), 255, np.uint8)
    photo[400:475, 60:135] = cv2.cvtColor(marker_image(75), cv2.COLOR_GRAY2BGR)
    mask = np.zeros((1000, 800), np.float32)
    mask[100:901, 340 if side else 300:460 if side else 500] = 1
    points = [SimpleNamespace(x=.5, y=.14, visibility=.99) for _ in range(33)]
    positions = {11:(390 if side else 300,260),12:(410 if side else 500,260),
                 23:(390,520),24:(410,520),25:(390,700),26:(410,700),
                 27:(390,850),28:(410,850),29:(390,890),30:(410,890),31:(385,890),32:(415,890)}
    for i,(x,y) in positions.items():
        points[i].x=x/800;points[i].y=y/1000
    return photo, points, mask


class MeasurementTests(unittest.TestCase):
    def test_marker_roundtrip_and_scale(self):
        photo, _, _ = fixture()
        scale, quality = detect_scale(photo)
        self.assertAlmostEqual(scale, 74/15, places=2)
        self.assertGreater(quality, .95)
        self.assertIn('width="150mm"',marker_svg())

    def test_missing_wrong_id_and_too_small_marker(self):
        for size, ident in [(75,1),(25,0)]:
            photo=np.full((600,600,3),255,np.uint8)
            marker=cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),ident,size)
            photo[100:100+size,100:100+size]=cv2.cvtColor(marker,cv2.COLOR_GRAY2BGR)
            with self.assertRaises(MeasurementError):detect_scale(photo)
        with self.assertRaisesRegex(MeasurementError,'not detected'):
            detect_scale(np.full((600,600,3),255,np.uint8))

    def test_uint8_falling_edge_regression_and_center_selection(self):
        mask=np.zeros((10,30),bool);mask[:,2:4]=True;mask[:,10:25]=True
        self.assertEqual(torso_width(mask,5,15,3),15)
        with self.assertRaises(MeasurementError):torso_width(mask,5,8,3)

    def test_height_and_waist_use_marker_not_entered_height(self):
        front, lm, mask=fixture()
        with patch('app.services.measurement_service.detect_pose',return_value=(lm,mask)):
            view=analyze_view(front)
        self.assertAlmostEqual(view.height_cm,800/(74/15),places=2)
        self.assertAlmostEqual(view.shoulder_cm,200/(74/15),places=2)
        self.assertAlmostEqual(view.waist_axis_cm,200/(74/15),places=2)
        self.assertFalse(view.fallback)

    def test_two_view_measurement(self):
        front,fl,fm=fixture();side,sl,sm=fixture(True)
        with patch('app.services.measurement_service.detect_pose',side_effect=[(fl,fm),(sl,sm)]):
            result=measure_person(front,side)
        self.assertEqual(result['status'],'good')
        self.assertAlmostEqual(result['waist_cm'],ellipse_circumference(200/(74/15),120/(74/15)),delta=.1)
        self.assertGreaterEqual(result['confidence'],.85)

    def test_pose_visibility_cropping_seated_and_wrong_side(self):
        photo,lm,mask=fixture();lm[11].visibility=.1
        with patch('app.services.measurement_service.detect_pose',return_value=(lm,mask)):
            with self.assertRaisesRegex(MeasurementError,'Shoulders'):analyze_view(photo)
        photo,lm,mask=fixture();mask[:100,300:500]=1
        with patch('app.services.measurement_service.detect_pose',return_value=(lm,mask)):
            with self.assertRaisesRegex(MeasurementError,'Full body'):analyze_view(photo)
        photo,lm,mask=fixture()
        for i in (25,26):lm[i].x=.9;lm[i].y=.52
        with patch('app.services.measurement_service.detect_pose',return_value=(lm,mask)):
            with self.assertRaisesRegex(MeasurementError,'straight legs'):analyze_view(photo)
        photo,lm,mask=fixture()
        with patch('app.services.measurement_service.detect_pose',return_value=(lm,mask)):
            with self.assertRaisesRegex(MeasurementError,'front-facing'):measure_person(photo,photo)

    def test_fallback_bounds_are_labelled_review(self):
        front,fl,fm=fixture();side,sl,sm=fixture(True)
        # Force an outline that misses the crown; pose fallback provides height.
        fm[:145]=0;sm[:145]=0
        with patch('app.services.measurement_service.detect_pose',side_effect=[(fl,fm),(sl,sm)]):
            result=measure_person(front,side)
        self.assertEqual(result['status'],'review')
        self.assertLessEqual(result['confidence'],.79)
        self.assertIn('fallback',result['notes'])

    def test_low_confidence_and_inconsistent_scales_rejected(self):
        front,fl,fm=fixture();side,sl,sm=fixture(True)
        with patch('app.services.measurement_service.detect_pose',side_effect=[(fl,fm),(sl,sm)]),patch('app.services.measurement_service.detect_scale',return_value=(5,.01)):
            # Force low visibility but above validation minima.
            for lm in fl+sl:lm.visibility=.51
            with self.assertRaisesRegex(MeasurementError,'Retake recommended'):measure_person(front,side)
        front,fl,fm=fixture();side,sl,sm=fixture(True)
        with patch('app.services.measurement_service.detect_pose',side_effect=[(fl,fm),(sl,sm)]),patch('app.services.measurement_service.detect_scale',side_effect=[(5,1),(8,1)]):
            with self.assertRaisesRegex(MeasurementError,'scales disagree'):measure_person(front,side)

    def test_decode_corrupt_tiny_and_exif_orientation(self):
        for data in (b'',b'not an image'):
            with self.assertRaises(MeasurementError):decode_image(data)
        tiny=io.BytesIO();Image.new('RGB',(20,20)).save(tiny,format='PNG')
        with self.assertRaisesRegex(MeasurementError,'resolution'):decode_image(tiny.getvalue())
        photo=io.BytesIO();exif=Image.Exif();exif[274]=6
        Image.new('RGB',(600,800)).save(photo,format='JPEG',exif=exif)
        self.assertEqual(decode_image(photo.getvalue()).shape,(600,800,3))

    def test_ellipse(self):
        self.assertAlmostEqual(ellipse_circumference(20,20),20*math.pi)
        with self.assertRaises(MeasurementError):ellipse_circumference(0,20)

    def test_multiple_people_are_rejected(self):
        fake=SimpleNamespace(detect=lambda image:SimpleNamespace(pose_landmarks=[[],[]]))
        with patch('app.services.pose_service.initialize_pose'),patch('app.services.pose_service._detector',fake):
            with self.assertRaisesRegex(MeasurementError,'More than one'):detect_pose(np.zeros((600,600,3),np.uint8))


if __name__=='__main__':unittest.main()
