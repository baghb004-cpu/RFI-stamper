"""Headless tests for the Lookout panorama math (gui/pano.py's pure parts):
equirect->perspective reprojection directions, panorama detection, image
loading via fitz, and the camera-grid cache."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                    # noqa: E402

from rfi_stamper.gui import pano                      # noqa: E402


def band_image():
    """Synthetic equirect: colored bands at the four compass longitudes and
    a bright band at the zenith."""
    W, H = 720, 360
    img = np.zeros((H, W, 3), np.uint8)
    img[:, :, 0] = 40
    lon = (np.arange(W) / W - 0.5) * 360
    img[:, np.abs(lon) < 30] = [200, 30, 30]            # front  = red
    img[:, np.abs(lon - 90) < 30] = [30, 200, 30]       # right  = green
    img[:, np.abs(np.abs(lon) - 180) < 30] = [30, 30, 200]  # back = blue
    img[:, np.abs(lon + 90) < 30] = [200, 200, 30]      # left   = yellow
    img[:40, :] = [240, 240, 240]                       # zenith = white
    return img


def main():
    img = band_image()
    assert pano.is_panorama(img)
    assert not pano.is_panorama(np.zeros((100, 150, 3), np.uint8))
    assert not pano.is_panorama(np.zeros((0, 10, 3), np.uint8))

    def center(yaw, pitch, fov=75):
        v = pano.reproject(img, yaw, pitch, fov, 200, 120)
        assert v.shape == (120, 200, 3) and v.dtype == np.uint8
        return tuple(int(c) for c in v[60, 100])

    assert center(0, 0) == (200, 30, 30), "yaw 0 faces the red band"
    assert center(90, 0) == (30, 200, 30), "yaw +90 turns right to green"
    assert center(-90, 0) == (200, 200, 30), "yaw -90 turns left to yellow"
    assert center(180, 0) == (30, 30, 200), "yaw 180 faces back/blue"
    assert center(0, 84, 60) == (240, 240, 240), "pitch up sees the zenith"
    assert center(360, 0) == center(0, 0), "yaw wraps"

    # grid cache: repeated same-size calls reuse; eviction never grows
    pano._GRID_CACHE.clear()
    for i in range(pano._GRID_CACHE_MAX + 4):
        pano.reproject(img, 0, 0, 40 + i, 64, 48)
    assert len(pano._GRID_CACHE) <= pano._GRID_CACHE_MAX

    # load_image_rgb round-trips a fitz-written image, any colorspace
    import fitz
    tmp = tempfile.mkdtemp(prefix="pano_")
    p = os.path.join(tmp, "t.png")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 30), False)
    pix.clear_with(90)
    pix.save(p)
    arr = pano.load_image_rgb(p)
    assert arr.shape == (30, 60, 3) and int(arr[0, 0, 0]) == 90

    print("PANO TESTS PASSED")


if __name__ == "__main__":
    main()
