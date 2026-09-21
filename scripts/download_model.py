"""Download the compatible pose model during Docker build, retrying transient errors."""
from pathlib import Path
import sys
import time
import urllib.request

URL = 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task'

def download(out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size >= 1_000_000:
        return out
    temporary = out.with_suffix('.download')
    for attempt in range(3):
        try:
            print(f'Downloading MediaPipe model (attempt {attempt+1}/3)...', flush=True)
            with urllib.request.urlopen(URL, timeout=90) as source, temporary.open('wb') as target:
                while chunk := source.read(1024*1024):
                    target.write(chunk)
            if temporary.stat().st_size < 1_000_000:
                raise RuntimeError('Model download is incomplete')
            temporary.replace(out)
            return out
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(3)

if __name__ == '__main__':
    print(download(sys.argv[1] if len(sys.argv)>1 else 'models/pose_landmarker_full.task'))
