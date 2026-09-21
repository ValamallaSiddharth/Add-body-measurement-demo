"""Generate a printable HTML page and SVG; run inside the Docker container."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.aruco_service import marker_page, marker_svg, marker_image
import cv2


def generate(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "print_marker.html").write_text(marker_page(), encoding="utf-8")
    (destination / "aruco_15cm.svg").write_text(marker_svg(), encoding="utf-8")
    png = cv2.copyMakeBorder(marker_image(), 75, 75, 75, 75, cv2.BORDER_CONSTANT, value=255)
    if not cv2.imwrite(str(destination / "aruco_marker.png"), png):
        raise RuntimeError("Could not write marker PNG")
    print(f"Open {destination / 'print_marker.html'} and print at 100%. Verify the BLACK square is exactly 15 x 15 cm with a ruler.")


if __name__ == "__main__":
    generate(sys.argv[1] if len(sys.argv) > 1 else "/app/data/images/marker")
