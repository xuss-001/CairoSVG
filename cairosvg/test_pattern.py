"""
Regression tests for SVG pattern coordinate systems and pattern reuse.

The native in-memory format of cairo image surfaces is premultiplied BGRA,
which is the byte order used by the pixel helper below.

"""

import io

import cairocffi as cairo

from . import svg2pdf, svg2png, svg2ps, svg2svg

RED = (0, 0, 255, 255)
WHITE = (255, 255, 255, 255)
BLUE = (255, 0, 0, 255)
GREEN = (0, 128, 0, 255)
YELLOW = (0, 255, 255, 255)
TEAL = (128, 128, 0, 255)
PURPLE = (128, 0, 128, 255)
TRANSPARENT = (0, 0, 0, 0)


def rasterize(svg, width=200, height=100):
    png = svg2png(svg, output_width=width, output_height=height)
    return cairo.ImageSurface.create_from_png(io.BytesIO(png))


def pixel(surface, x, y):
    data = surface.get_data()
    offset = y * surface.get_stride() + x * 4
    return tuple(data[offset:offset + 4])


def assert_color(surface, x, y, expected, tolerance=10):
    actual = pixel(surface, x, y)
    assert actual[3] > 255 - tolerance
    for channel in range(3):
        assert abs(actual[channel] - expected[channel]) <= tolerance


def test_object_bounding_box_uses_target_origin():
    """Pattern tiles restart at the origin of each target bounding box."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"
        viewBox="0 0 200 100">
      <defs><pattern id="p" width="0.25" height="0.25">
        <rect width="100%" height="100%" fill="white"/>
        <rect width="50%" height="50%" fill="red"/>
      </pattern></defs>
      <rect x="0" y="0" width="80" height="80" fill="url(#p)"/>
      <rect x="100" y="10" width="80" height="80" fill="url(#p)"/>
    </svg>'''
    surface = rasterize(svg)
    # A tile is 20x20 user units, its top-left quadrant is red.
    assert_color(surface, 5, 5, RED)
    assert_color(surface, 15, 15, WHITE)
    # The second rect gets its own tiling anchored on its bbox origin.
    assert_color(surface, 105, 15, RED)
    assert_color(surface, 115, 25, WHITE)
    assert_color(surface, 125, 15, RED)
    # Equivalent points of both references render identically.
    assert pixel(surface, 5, 5) == pixel(surface, 105, 15)


def test_reused_pattern_keeps_size_and_color():
    """Reusing a pattern must not grow the tile or fade the content."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"
        viewBox="0 0 200 100">
      <defs><pattern id="p" width="0.25" height="0.25">
        <rect width="100%" height="100%" fill="white"/>
        <rect width="50%" height="50%" fill="red"/>
      </pattern></defs>
      <rect x="0" y="0" width="80" height="80" fill="url(#p)"/>
      <rect x="100" y="10" width="80" height="80" fill="url(#p)"/>
      <rect x="0" y="0" width="1" height="1" fill="url(#p)"/>
    </svg>'''
    surface = rasterize(svg)
    assert_color(surface, 5, 5, RED)
    assert_color(surface, 105, 15, RED)


def test_swap_non_overlapping_objects_is_stable():
    """Swapping disjoint objects does not change the rendered image."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"
        viewBox="0 0 200 100">
      <defs><pattern id="p" width="0.25" height="0.25">
        <rect width="100%" height="100%" fill="white"/>
        <rect width="50%" height="50%" fill="red"/>
      </pattern></defs>
      <rect x="0" y="0" width="80" height="80" fill="url(#p)"/>
      <rect x="100" y="10" width="80" height="80" fill="url(#p)"/>
    </svg>'''
    first = b'<rect x="0" y="0" width="80" height="80" fill="url(#p)"/>'
    second = b'<rect x="100" y="10" width="80" height="80" fill="url(#p)"/>'
    swapped = svg.replace(first + b'\n      ' + second,
                          second + b'\n      ' + first)
    assert swapped != svg
    assert rasterize(svg).get_data() == rasterize(swapped).get_data()


def test_opacity_is_not_accumulated_between_references():
    """The same pattern with different opacities stays independent."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60"
        viewBox="0 0 200 60">
      <defs><pattern id="p" width="8" height="8"
          patternUnits="userSpaceOnUse">
        <rect width="8" height="8" fill="red"/>
      </pattern></defs>
      <rect x="0" y="0" width="80" height="40" fill="url(#p)"
          fill-opacity="0.5"/>
      <rect x="100" y="0" width="80" height="40" fill="url(#p)"
          fill-opacity="0.5"/>
      <rect x="50" y="50" width="8" height="8" fill="url(#p)"
          fill-opacity="0.25"/>
    </svg>'''
    surface = rasterize(svg, 200, 60)
    # Premultiplied red at 50% and 25% alpha.
    assert pixel(surface, 10, 10) == (0, 0, 128, 128)
    assert pixel(surface, 110, 10) == (0, 0, 128, 128)
    assert pixel(surface, 52, 52) == (0, 0, 64, 64)


def test_pattern_opacity_attribute_is_applied_once():
    """An opacity defined on the pattern itself does not stack."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="60" height="30"
        viewBox="0 0 60 30">
      <defs><pattern id="p" width="8" height="8"
          patternUnits="userSpaceOnUse" opacity="0.5">
        <rect width="8" height="8" fill="red"/>
      </pattern></defs>
      <rect x="0" y="0" width="20" height="20" fill="url(#p)"/>
      <rect x="30" y="0" width="20" height="20" fill="url(#p)"/>
    </svg>'''
    surface = rasterize(svg, 60, 30)
    assert pixel(surface, 5, 5) == (0, 0, 128, 128)
    assert pixel(surface, 35, 5) == (0, 0, 128, 128)


def test_user_space_percentages_resolve_against_viewport():
    """Percentage tiles in user space use the viewport dimensions."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"
        viewBox="0 0 200 100">
      <defs><pattern id="p" width="10%" height="10%"
          patternUnits="userSpaceOnUse">
        <rect width="100%" height="100%" fill="white"/>
        <rect width="50%" height="50%" fill="blue"/>
      </pattern></defs>
      <rect x="30" y="20" width="160" height="70" fill="url(#p)"/>
    </svg>'''
    surface = rasterize(svg)
    # Tile of 20x10 anchored at the user-space origin, blue half of 10x5.
    assert_color(surface, 44, 24, BLUE)
    assert_color(surface, 55, 24, WHITE)
    assert_color(surface, 44, 28, WHITE)
    assert_color(surface, 64, 24, BLUE)
    assert_color(surface, 44, 34, BLUE)


def test_view_box_and_preserve_aspect_ratio_alignment():
    """viewBox and preserveAspectRatio align content inside the tile."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"
        viewBox="0 0 200 100">
      <defs><pattern id="p" width="40" height="40"
          patternUnits="userSpaceOnUse" viewBox="0 0 10 10"
          preserveAspectRatio="xMidYMid meet">
        <rect width="10" height="10" fill="yellow"/>
        <rect width="5" height="5" fill="green"/>
      </pattern></defs>
      <rect x="20" y="10" width="120" height="80" fill="url(#p)"/>
    </svg>'''
    surface = rasterize(svg)
    # The 5x5 green corner scales to 20x20 and sits at the right of the
    # centered 40x40 viewBox viewport.
    assert_color(surface, 45, 40, GREEN)
    assert_color(surface, 25, 25, YELLOW)


def test_non_square_tile_aspect_ratio_modes():
    """meet, slice and none behave differently on non-square tiles."""
    base = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"
        viewBox="0 0 200 100">
      <defs><pattern id="p" width="40" height="20"
          patternUnits="userSpaceOnUse" viewBox="0 0 10 10"
          preserveAspectRatio="ALIGN">
        <rect width="10" height="10" fill="yellow"/>
        <rect width="5" height="5" fill="green"/>
      </pattern></defs>
      <rect width="120" height="60" fill="url(#p)"/>
    </svg>'''
    meet = rasterize(base.replace(b'ALIGN', b'xMidYMid meet'))
    sliced = rasterize(base.replace(b'ALIGN', b'xMidYMid slice'))
    stretched = rasterize(base.replace(b'ALIGN', b'none'))
    # meet centers a scale-2 green quadrant (10x10 inside the 40x20 tile).
    assert_color(meet, 15, 5, GREEN)
    assert_color(meet, 25, 5, YELLOW)
    # slice uses the larger scale and crops the viewport.
    assert_color(sliced, 15, 5, GREEN)
    assert_color(sliced, 25, 5, YELLOW)
    # none stretches the green quadrant to 20x10.
    assert_color(stretched, 15, 5, GREEN)
    assert_color(stretched, 15, 15, YELLOW)


def test_pattern_transform_changes_tiling():
    """patternTransform is applied without changing the drawing context."""
    base = b'''<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"
        viewBox="0 0 120 120">
      <defs><pattern id="p" width="20" height="20"
          patternUnits="userSpaceOnUse" TRANSFORM>
        <rect width="20" height="20" fill="white"/>
        <rect width="10" height="10" fill="red"/>
      </pattern></defs>
      <rect width="120" height="120" fill="url(#p)"/>
    </svg>'''
    plain = rasterize(base.replace(b'TRANSFORM', b''), 120, 120)
    rotated = rasterize(
        base.replace(b'TRANSFORM', b'patternTransform="rotate(45)"'),
        120, 120)
    assert plain.get_data() != rotated.get_data()
    assert any(
        pixel(rotated, x, y)[:3] == RED[:3]
        for x in range(0, 120, 4) for y in range(0, 120, 4))


def test_pattern_transform_scale_and_translate():
    """Scaling and translating apply to the pattern coordinate system."""
    base = b'''<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"
        viewBox="0 0 120 120">
      <defs><pattern id="p" width="20" height="20"
          patternUnits="userSpaceOnUse" TRANSFORM>
        <rect width="20" height="20" fill="white"/>
        <rect width="10" height="10" fill="red"/>
      </pattern></defs>
      <rect width="120" height="120" fill="url(#p)"/>
    </svg>'''
    scaled = rasterize(
        base.replace(b'TRANSFORM', b'patternTransform="scale(2)"'),
        120, 120)
    # The tile doubles to 40x40 and its red quadrant to 20x20.
    assert_color(scaled, 10, 10, RED)
    assert_color(scaled, 25, 10, WHITE)
    assert_color(scaled, 45, 10, RED)
    translated = rasterize(
        base.replace(b'TRANSFORM', b'patternTransform="translate(10 0)"'),
        120, 120)
    # The tiling origin shifts by 10 user units.
    assert_color(translated, 5, 5, WHITE)
    assert_color(translated, 15, 5, RED)


def test_inner_transform_is_preserved():
    """Transforms of the pattern contents are not lost."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"
        viewBox="0 0 100 100">
      <defs><pattern id="p" width="20" height="20"
          patternUnits="userSpaceOnUse">
        <g transform="rotate(45)"><rect width="10" height="10" fill="red"/></g>
      </pattern></defs>
      <rect width="100" height="100" fill="url(#p)"/>
    </svg>'''
    rotated = rasterize(svg, 100, 100)
    plain_svg = svg.replace(b'<g transform="rotate(45)">', b'<g>')
    plain = rasterize(plain_svg, 100, 100)
    assert rotated.get_data() != plain.get_data()


def test_pattern_content_units_object_bounding_box():
    """Contents in objectBoundingBox units scale with the target box."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"
        viewBox="0 0 200 100">
      <defs><pattern id="p" width="1" height="1"
          patternContentUnits="objectBoundingBox">
        <rect width="0.5" height="0.5" fill="red"/>
      </pattern></defs>
      <rect x="20" y="20" width="100" height="60" fill="url(#p)"/>
    </svg>'''
    surface = rasterize(svg)
    # 0.5 bbox unit maps to 50px wide and 30px high from the bbox origin.
    assert_color(surface, 40, 40, RED)
    assert pixel(surface, 80, 60) == TRANSPARENT


def test_stroke_uses_pattern_and_keeps_transform():
    """Patterns apply to strokes, including shapes with a transform."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"
        viewBox="0 0 100 100">
      <defs><pattern id="p" width="10" height="10"
          patternUnits="userSpaceOnUse">
        <rect width="10" height="10" fill="white"/>
        <rect width="5" height="5" fill="purple"/>
      </pattern></defs>
      <g transform="translate(5 5)">
        <rect x="10" y="10" width="60" height="60" fill="none"
            stroke="url(#p)" stroke-width="8"/>
      </g>
    </svg>'''
    surface = rasterize(svg, 100, 100)
    assert any(
        pixel(surface, x, 15)[:3] == PURPLE[:3]
        for x in range(18, 72, 2))


def test_local_href_inherits_pattern():
    """A pattern referencing another one inherits its full definition."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"
        viewBox="0 0 100 100">
      <defs>
        <pattern id="base" width="20" height="20"
            patternUnits="userSpaceOnUse">
          <rect width="20" height="20" fill="white"/>
          <rect width="10" height="10" fill="teal"/>
        </pattern>
        <pattern id="derived" href="#base"/>
      </defs>
      <rect width="100" height="100" fill="url(#derived)"/>
    </svg>'''
    surface = rasterize(svg, 100, 100)
    assert_color(surface, 5, 5, TEAL)


def test_zero_sized_tiles_are_ignored():
    """Zero-width or zero-height tiles end without crashing."""
    cases = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50">'
        b'<defs><pattern id="p" width="0" height="10">'
        b'<rect width="5" height="5" fill="red"/></pattern></defs>'
        b'<rect width="40" height="40" fill="url(#p)"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50">'
        b'<defs><pattern id="p" width="10" height="0">'
        b'<rect width="5" height="5" fill="red"/></pattern></defs>'
        b'<rect width="40" height="40" fill="url(#p) red"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50">'
        b'<defs><pattern id="p" width="0" height="0" '
        b'patternUnits="userSpaceOnUse">'
        b'<rect width="5" height="5" fill="red"/></pattern></defs>'
        b'<rect width="40" height="40" fill="url(#p)"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50">'
        b'<defs><pattern id="p" width="-5" height="10" '
        b'patternUnits="userSpaceOnUse">'
        b'<rect width="5" height="5" fill="red"/></pattern></defs>'
        b'<rect width="40" height="40" fill="url(#p) red"/></svg>',
    )
    for svg in cases:
        rasterize(svg, 50, 50)


def test_empty_pattern_uses_fallback_color():
    """Patterns without drawable content fall back to the solid color."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50"
        viewBox="0 0 50 50">
      <defs>
        <pattern id="empty" width="10" height="10"></pattern>
        <pattern id="empty-box" width="0.5" height="0.5"></pattern>
      </defs>
      <rect x="0" y="0" width="25" height="50" fill="url(#empty) lime"/>
      <rect x="25" y="0" width="25" height="50" fill="url(#empty-box) lime"/>
    </svg>'''
    surface = rasterize(svg, 50, 50)
    assert_color(surface, 10, 10, (0, 255, 0, 255))
    assert_color(surface, 35, 10, (0, 255, 0, 255))


def test_cyclic_pattern_references_terminate():
    """Direct and indirect cyclic references stop without an infinite loop."""
    direct = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50">'
        b'''
      <defs><pattern id="a" width="10" height="10">
        <rect width="10" height="10" fill="url(#a)"/>
      </pattern></defs>
      <rect width="40" height="40" fill="url(#a) red"/>
    </svg>''')
    indirect = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50">'
        b'''
      <defs>
        <pattern id="a" width="10" height="10">
          <rect width="10" height="10" fill="url(#b)"/>
        </pattern>
        <pattern id="b" width="10" height="10">
          <rect width="10" height="10" fill="url(#a)"/>
        </pattern>
      </defs>
      <rect width="40" height="40" fill="url(#a)"/>
    </svg>''')
    rasterize(direct, 50, 50)
    rasterize(indirect, 50, 50)


def test_png_and_vector_exports_share_tiling():
    """PNG and the SVG vector export rasterize to the same checker tiling."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90"
        viewBox="0 0 160 90">
      <defs><pattern id="p" width="20" height="10"
          patternUnits="userSpaceOnUse">
        <rect width="10" height="10" fill="red"/>
        <rect x="10" width="10" height="10" fill="white"/>
        <rect y="5" width="10" height="5" fill="white"/>
        <rect x="10" y="5" width="10" height="5" fill="red"/>
      </pattern></defs>
      <rect x="30" y="15" width="100" height="60" fill="url(#p)"/>
    </svg>'''
    png_surface = rasterize(svg, 160, 90)
    exported_svg = svg2svg(svg, output_width=160, output_height=90)
    vector_surface = cairo.ImageSurface.create_from_png(io.BytesIO(
        svg2png(bytestring=exported_svg, output_width=160,
                output_height=90)))
    assert png_surface.get_data() == vector_surface.get_data()


def test_all_vector_formats_embed_patterns():
    """Patterned documents can be exported to SVG, PDF and PostScript."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40"
        viewBox="0 0 60 40">
      <defs><pattern id="p" width="10" height="10"
          patternUnits="userSpaceOnUse">
        <rect width="10" height="10" fill="red"/>
      </pattern></defs>
      <rect width="60" height="40" fill="url(#p)"/>
    </svg>'''
    assert svg2svg(svg).startswith(b'<?xml')
    assert svg2pdf(svg).startswith(b'%PDF')
    assert svg2ps(svg).startswith(b'%!PS')
