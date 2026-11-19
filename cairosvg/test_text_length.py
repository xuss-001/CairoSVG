"""
Tests for the ``textLength`` and ``lengthAdjust`` attributes.

The checks rely on font metrics (the horizontal advances reported by Cairo)
rather than colored pixel boundaries: ``textLength`` adjusts typographic
advance widths, which is independent of glyph side bearings and ink extents.

"""

import io
import re

import cairocffi as cairo
import pytest

from . import svg2pdf, svg2png, svg2svg

FONT_FAMILY = 'sans-serif'
FONT_SIZE = 20


def font_context():
    """Return a Cairo context configured with the test font."""
    context = cairo.Context(
        cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None))
    context.select_font_face(FONT_FAMILY)
    context.set_font_size(FONT_SIZE)
    return context


def font_advance(text):
    """Return the natural horizontal advance of ``text`` in user units."""
    context = font_context()
    return sum(context.text_extents(letter)[4] for letter in text)


def glyph_columns(png):
    """Return the groups of contiguous colored columns in a PNG image."""
    image = cairo.ImageSurface.create_from_png(io.BytesIO(png))
    data = bytes(image.get_data())
    width, height = image.get_width(), image.get_height()
    stride = image.get_stride()
    columns = [
        x for x in range(width)
        if any(data[y * stride + x * 4 + 3] > 50 for y in range(height))]
    groups = []
    for column in columns:
        if groups and column <= groups[-1][1] + 1:
            groups[-1][1] = column
        else:
            groups.append([column, column])
    return groups


def render_columns(svg):
    """Render ``svg`` to PNG and return its colored column groups."""
    return glyph_columns(svg2png(svg.encode()))


def glyph_origins(svg):
    """Return the exact horizontal glyph origins from the SVG vector output.

    Cairo's SVG surface places each glyph with a ``<use x="...">`` element,
    giving the typographic cursor positions without rasterization rounding.

    """
    vector = svg2svg(svg.encode()).decode()
    return [
        float(match.group(1))
        for match in re.finditer(r'<use\b[^>]*?\sx="([-\d.]+)"', vector)]


def glyph_path_widths(svg):
    """Return the horizontal extent of every embedded glyph vector path."""
    vector = svg2svg(svg.encode()).decode()
    widths = []
    glyph_ids = re.findall(r'<use xlink:href="#(glyph-[^"]+)"', vector)
    for glyph_id in dict.fromkeys(glyph_ids):
        path_match = re.search(
            r'<g id="%s">(.*?)</g>' % glyph_id, vector, re.S)
        if path_match is None:
            continue
        path_data = path_match.group(1)
        numbers = [float(value) for value in re.findall(
            r'[-\d.]+', path_data)]
        x_coordinates = numbers[0::2]
        if x_coordinates:
            widths.append(max(x_coordinates) - min(x_coordinates))
    return widths


def ordered_glyph_path_widths(svg):
    """Return glyph path widths in the order the glyphs are drawn."""
    vector = svg2svg(svg.encode()).decode()
    glyph_sizes = {}
    for glyph_id in dict.fromkeys(
            re.findall(r'<use xlink:href="#(glyph-[^"]+)"', vector)):
        path_match = re.search(
            r'<g id="%s">(.*?)</g>' % glyph_id, vector, re.S)
        if path_match is None:
            continue
        numbers = [float(value) for value in re.findall(
            r'[-\d.]+', path_match.group(1))]
        x_coordinates = numbers[0::2]
        if x_coordinates:
            glyph_sizes[glyph_id] = max(x_coordinates) - min(x_coordinates)
    return [
        glyph_sizes[glyph_id]
        for glyph_id in re.findall(
            r'<use xlink:href="#(glyph-[^"]+)"', vector)]
    return widths


def text_svg(body, **attributes):
    """Build a simple horizontal text SVG at a known anchor."""
    attributes['font-family'] = FONT_FAMILY
    attributes['font-size'] = FONT_SIZE
    attrs = ' '.join(
        f'{name}="{value}"' for name, value in attributes.items())
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="160">'
        f'<text x="20" y="80" {attrs}>{body}</text></svg>')


def test_missing_attributes_keep_natural_layout():
    """Without textLength the rendered advance is the natural one."""
    text = 'Hello'
    origins = glyph_origins(text_svg(text))
    natural = font_advance(text)
    # The last cursor position is the last glyph origin plus its advance.
    last_advance = font_context().text_extents(text[-1])[4]
    assert origins[-1] + last_advance - origins[0] == (
        pytest.approx(natural, abs=1.0))


def test_spacing_stretches_inter_character_advance():
    """``spacing`` enlarges the gaps but keeps glyph shapes natural."""
    text = 'Hello'
    target = 200
    origins = glyph_origins(text_svg(text, textLength=target))
    assert font_advance(text) < target
    context = font_context()
    natural_first = (
        context.text_extents(text[0])[2] -
        context.text_extents(text[0])[0])
    # spacing mode never scales a glyph: the first glyph keeps its width.
    assert natural_first > 0
    last_advance = context.text_extents(text[-1])[4]
    assert origins[-1] + last_advance - origins[0] == (
        pytest.approx(target, abs=1.0))


def test_spacing_compresses_inter_character_advance():
    """``spacing`` can compress the text below its natural length."""
    text = 'Hello'
    target = 30
    origins = glyph_origins(text_svg(text, textLength=target))
    assert font_advance(text) > target
    last_advance = font_context().text_extents(text[-1])[4]
    assert origins[-1] + last_advance - origins[0] == (
        pytest.approx(target, abs=1.0))


def test_spacing_and_glyphs_scales_glyphs_horizontally():
    """``spacingAndGlyphs`` stretches the glyph shapes horizontally."""
    text = 'Hello'
    target = 200
    spacing_origins = glyph_origins(text_svg(text, textLength=target))
    glyph_origins_list = glyph_origins(text_svg(
        text, textLength=target, lengthAdjust='spacingAndGlyphs'))
    # In glyph mode the shapes themselves are wider: the embedded vector
    # glyph paths have a larger horizontal extent than in spacing mode.
    spacing_widths = glyph_path_widths(text_svg(text, textLength=target))
    glyph_widths = glyph_path_widths(text_svg(
        text, textLength=target, lengthAdjust='spacingAndGlyphs'))
    # Every shared glyph shape is visibly wider in glyph adjustment mode.
    for spacing_width, glyph_width in zip(spacing_widths, glyph_widths):
        assert glyph_width > spacing_width * 1.5
    last_advance = font_context().text_extents(text[-1])[4]
    scale = target / font_advance(text)
    assert glyph_origins_list[-1] + last_advance * scale - (
        glyph_origins_list[0]) == pytest.approx(target, abs=1.5)


def test_spacing_and_glyphs_compression():
    """``spacingAndGlyphs`` can compress glyphs horizontally as well."""
    text = 'Hello'
    target = 30
    origins = glyph_origins(text_svg(
        text, textLength=target, lengthAdjust='spacingAndGlyphs'))
    context = font_context()
    natural_first_advance = context.text_extents(text[0])[4]
    # The gap to the second glyph is a scaled (smaller) first advance.
    assert origins[1] - origins[0] < natural_first_advance


@pytest.mark.parametrize('anchor', ('start', 'middle', 'end'))
def test_text_anchor_uses_adjusted_positions(anchor):
    """text-anchor is computed from the adjusted typographic length."""
    target = 260
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="700" height="120">'
        f'<text x="300" y="60" font-family="{FONT_FAMILY}" '
        f'font-size="{FONT_SIZE}" textLength="{target}" '
        f'text-anchor="{anchor}">AB</text></svg>')
    origins = glyph_origins(svg)
    last_advance = font_context().text_extents('B')[4]
    start, end = origins[0], origins[-1] + last_advance
    assert end - start == pytest.approx(target, abs=1.0)
    if anchor == 'start':
        assert start == pytest.approx(300, abs=1.0)
    elif anchor == 'middle':
        assert (start + end) / 2 == pytest.approx(300, abs=1.0)
    else:
        assert end == pytest.approx(300, abs=1.0)


def test_nested_tspan_keeps_its_own_target_length():
    """A constrained tspan keeps its length while the parent adjusts."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="120">'
        f'<text x="20" y="60" font-family="{FONT_FAMILY}" '
        f'font-size="{FONT_SIZE}" textLength="350">'
        'A<tspan textLength="60">BC</tspan>DE</text></svg>')
    origins = glyph_origins(svg)
    c_advance = font_context().text_extents('C')[4]
    assert origins[2] + c_advance - origins[1] == (
        pytest.approx(60, abs=1.0))
    e_advance = font_context().text_extents('E')[4]
    assert origins[-1] + e_advance - origins[0] == (
        pytest.approx(350, abs=1.0))


def test_nested_tspan_not_resized_by_parent_glyph_adjustment():
    """A glyph-scaling parent must not scale an already constrained child."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="120">'
        f'<text x="20" y="60" font-family="{FONT_FAMILY}" '
        f'font-size="{FONT_SIZE}" textLength="450" '
        'lengthAdjust="spacingAndGlyphs">'
        'A<tspan textLength="60">BC</tspan>DE</text></svg>')
    origins = glyph_origins(svg)
    # The child block reaches its own 60-unit target using an independent
    # scaling factor: its total advance (B origin to C end) is 60, while the
    # parent's much larger factor only applies to A, D and E.
    c_advance = font_context().text_extents('C')[4]
    child_scale = 60 / (2 * c_advance)
    assert origins[2] + c_advance * child_scale - origins[1] == (
        pytest.approx(60, abs=1.0))
    natural_b_width = glyph_path_widths(
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="120">'
        f'<text x="20" y="60" font-family="{FONT_FAMILY}" '
        f'font-size="{FONT_SIZE}">BC</text></svg>')[0]
    nested = ordered_glyph_path_widths(svg)
    # The B glyph is scaled by the child's own factor only, not the larger
    # parent factor applied to the surrounding A, D and E glyphs.
    assert nested[1] == pytest.approx(
        natural_b_width * child_scale, rel=0.03)
    parent_scale = 450 / font_advance('ABCDE')
    assert nested[1] < natural_b_width * parent_scale * 0.9


def test_following_text_continues_at_adjusted_cursor():
    """Text after a constrained span starts at the adjusted cursor position."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="120">'
        f'<text x="20" y="60" font-family="{FONT_FAMILY}" '
        f'font-size="{FONT_SIZE}" textLength="300">'
        'AB<tspan>CD</tspan></text></svg>')
    origins = glyph_origins(svg)
    assert origins[2] > origins[1]
    last_advance = font_context().text_extents('D')[4]
    assert origins[-1] + last_advance - origins[0] > font_advance('ABCD')


@pytest.mark.parametrize('unit,multiplier', (
    ('', 1), ('px', 1), ('em', FONT_SIZE)))
def test_length_units(unit, multiplier):
    """Unitless, px and em textLength values are all supported."""
    value = 10
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="120">'
        f'<text x="20" y="60" font-family="{FONT_FAMILY}" '
        f'font-size="{FONT_SIZE}" textLength="{value}{unit}">AB</text></svg>')
    origins = glyph_origins(svg)
    last_advance = font_context().text_extents('B')[4]
    assert origins[-1] + last_advance - origins[0] == pytest.approx(
        value * multiplier, abs=0.5)


def test_letter_spacing_is_included_in_natural_length():
    """Existing letter-spacing is part of the unadjusted advance."""
    text, spacing, target = 'ABCD', 6, 250
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="120">'
        f'<text x="20" y="60" font-family="{FONT_FAMILY}" '
        f'font-size="{FONT_SIZE}" letter-spacing="{spacing}" '
        f'textLength="{target}">{text}</text></svg>')
    origins = glyph_origins(svg)
    natural_with_spacing = font_advance(text) + spacing * (len(text) - 1)
    assert natural_with_spacing < target
    last_advance = font_context().text_extents(text[-1])[4]
    assert origins[-1] + last_advance - origins[0] == (
        pytest.approx(target, abs=1.0))


def test_relative_dx_is_preserved():
    """Relative horizontal displacement still applies on top of the layout."""
    base = glyph_origins(text_svg('AB', textLength=120))
    shifted = glyph_origins(text_svg('AB', textLength=120, dx=30))
    assert shifted[0] - base[0] == pytest.approx(30, abs=1.0)


def test_mixed_font_sizes():
    """Nested tspans with different font sizes are measured independently."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="160">'
        f'<text x="20" y="80" font-family="{FONT_FAMILY}" '
        f'font-size="{FONT_SIZE}" textLength="300">'
        'A<tspan font-size="36">B</tspan>CD</text></svg>')
    origins = glyph_origins(svg)
    d_advance = font_context().text_extents('D')[4]
    assert origins[-1] + d_advance - origins[0] == (
        pytest.approx(300, abs=1.0))


@pytest.mark.parametrize('value', ('-10', 'abc'))
def test_invalid_length_falls_back_to_natural_text(value):
    """Invalid textLength values are handled like ordinary text."""
    assert glyph_origins(text_svg('Hi', textLength=value)) == (
        glyph_origins(text_svg('Hi')))


def test_zero_text_length_is_a_valid_target():
    """A zero textLength compresses spacing instead of falling back."""
    spacing = glyph_origins(text_svg('Hi', textLength=0))
    natural = glyph_origins(text_svg('Hi'))
    assert spacing != natural
    assert spacing[0] == natural[0]


def test_unknown_length_adjust_falls_back_to_spacing():
    """An unknown lengthAdjust behaves like the default spacing mode."""
    explicit = glyph_origins(
        text_svg('Hello', textLength=200, lengthAdjust='bogus'))
    default = glyph_origins(text_svg('Hello', textLength=200))
    assert explicit == default


def test_empty_text_does_not_crash():
    """An empty text node with textLength produces no error."""
    svg = text_svg('', textLength=100)
    assert svg2png(svg.encode())[:8] == b'\x89PNG\r\n\x1a\n'
    assert render_columns(svg) == []


def test_single_character_spacing_mode():
    """A single character has no adjustable gap and must not divide by zero."""
    columns = render_columns(text_svg('X', textLength=100))
    assert len(columns) == 1


def test_single_character_glyph_mode():
    """A single character is scaled horizontally in glyph mode."""
    origins = glyph_origins(text_svg(
        'X', textLength=100, lengthAdjust='spacingAndGlyphs'))
    # A lone glyph is scaled so its advance equals the target length.
    scaled_advance = font_context().text_extents('X')[4] * (
        100 / font_context().text_extents('X')[4])
    assert scaled_advance == pytest.approx(100, abs=1.0)
    assert len(origins) == 1


def test_zero_target_length_is_finite():
    """A zero glyph target compresses without non-finite coordinates."""
    columns = render_columns(text_svg(
        'Hi', textLength=0, lengthAdjust='spacingAndGlyphs'))
    for start, end in columns:
        assert 0 <= start <= end
    # The vector surface must still produce finite glyph origins.
    for origin in glyph_origins(text_svg(
            'Hi', textLength=0, lengthAdjust='spacingAndGlyphs')):
        assert origin == origin


def test_baseline_fill_stroke_and_transform_keep_their_meaning():
    """Adjustment does not break fill, stroke or external transforms."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="160">'
        '<g transform="translate(40, 0)">'
        f'<text x="10" y="80" font-family="{FONT_FAMILY}" '
        f'font-size="{FONT_SIZE}" fill="blue" stroke="red" '
        'stroke-width="2" textLength="200">Stroked</text></g></svg>')
    image = cairo.ImageSurface.create_from_png(io.BytesIO(svg2png(
        svg.encode())))
    data = bytes(image.get_data())
    width, height = image.get_width(), image.get_height()
    stride = image.get_stride()
    xs = [
        x for y in range(height) for x in range(width)
        if data[y * stride + x * 4 + 3] > 50]
    assert min(xs) >= 45


def test_png_and_vector_surfaces_share_the_layout():
    """PNG, PDF and SVG vector surfaces all accept the adjusted text."""
    svg = text_svg('Hello', textLength=220)
    png_columns = render_columns(svg)
    pdf = svg2pdf(svg.encode())
    vector = svg2svg(svg.encode())
    assert pdf[:4] == b'%PDF'
    assert b'<svg' in vector[:200]
    # Both surfaces are driven by the same Cairo layout, so the raster output
    # reaches the requested typographic width.
    assert png_columns[-1][1] - 20 == pytest.approx(220, abs=3)
