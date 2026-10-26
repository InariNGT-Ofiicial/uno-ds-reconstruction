"""1440p compositor: source-coordinate analysis and a separate native-art raster.

The 852x480 plane is used only for the existing event/graphic analysis. Native
1671/1672x941 artwork is sampled directly onto 2556x1440, then padded to 2560.
No completed 480p/720p video is used as the character raster.
"""
from functools import lru_cache
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
import render as core

SCALE = 3
INNER = (core.SIZE[0] * SCALE, core.SIZE[1] * SCALE)
OUTPUT = (2560, 1440)
legacy_normalize = core.normalize


def enlarge(im):
    native = getattr(im, '_hd_raster', None)
    if native is not None:
        return native
    return core.premultiplied_resize(im, (im.width * SCALE, im.height * SCALE))


@lru_cache(maxsize=24)
def native_normalize(value):
    low = legacy_normalize(value)
    original = core.image(value)
    if original.size == core.SIZE:
        high = core.premultiplied_resize(original, INNER)
    else:
        # Identical uniform scale/crop convention as v1; bypass the 480p raster.
        if original.height != 941 or original.width not in {1671, 1672}:
            raise ValueError(f'Unexpected native artwork canvas: {original.size}')
        high = original.convert('RGBa').resize(
            INNER, Image.Resampling.LANCZOS,
            box=(0, 0, 852 * 941 / 480, 941)).convert('RGBA')
    low._hd_raster = high
    return low


class Canvas:
    def __init__(self, low, high=None):
        self.low = low.copy()
        self.high = (enlarge(low) if high is None else high).copy()

    def __array__(self, dtype=None, copy=None):
        result = np.asarray(self.low, dtype=dtype)
        return result.copy() if copy else result

    def alpha_composite(self, im, dest=(0, 0), source=(0, 0)):
        self.low.alpha_composite(im, dest, source)
        self.high.alpha_composite(
            enlarge(im), tuple(round(v * SCALE) for v in dest),
            tuple(round(v * SCALE) for v in source))

    def paste(self, im, box=None, mask=None):
        self.low.paste(im, box, mask)
        high_im = enlarge(im) if isinstance(im, Image.Image) else im
        high_box = None if box is None else tuple(round(v * SCALE) for v in box)
        high_mask = None if mask is None else mask.resize(
            (mask.width * SCALE, mask.height * SCALE), Image.Resampling.BILINEAR)
        self.high.paste(high_im, high_box, high_mask)


class CanvasDraw:
    def __init__(self, canvas):
        self.low = ImageDraw.Draw(canvas.low)
        self.high = ImageDraw.Draw(canvas.high)

    def rectangle(self, xy, fill=None, outline=None, width=1):
        self.low.rectangle(xy, fill=fill, outline=outline, width=width)
        x0, y0, x1, y1 = xy
        # Pillow includes the final pixel, unlike [start,end) event bounds.
        high_xy = (x0 * SCALE, y0 * SCALE,
                   (x1 + 1) * SCALE - 1, (y1 + 1) * SCALE - 1)
        self.high.rectangle(high_xy, fill=fill, outline=outline, width=width * SCALE)


def blend_stage(a, b, amount):
    return Canvas(Image.blend(a.low, b, amount),
                  Image.blend(a.high, enlarge(b), amount))


def output_raster(canvas):
    result = Image.new('RGB', OUTPUT)
    result.paste(canvas.high.convert('RGB'), (2, 0))
    return result


def main():
    core.normalize = native_normalize
    core.stage_image = Canvas
    core.draw_stage = CanvasDraw
    core.blend_stage = blend_stage
    core.output_raster = output_raster
    core.OUTPUT_SIZE = OUTPUT
    core.DEFAULT_CRF = 14
    core.DEFAULT_PRESET = 'medium'
    core.RASTER_PIPELINE = {
        'name': 'native_art_to_1440p_with_source_coordinate_analysis',
        'analysis_size': list(core.SIZE),
        'native_art_height': 941,
        'character_raster': 'native artwork -> 2556x1440, no 480p intermediate',
        'outer_padding_pixels': [2, 2],
        'graphics': 'existing source-coordinate graphics; retain original source detail limits',
        'adapter_sha256': core.digest(Path(__file__)),
        'forbidden_operations': ['liquify', 'local actor warp', 'new pose interpolation']
    }
    core.main()


if __name__ == '__main__':
    main()
