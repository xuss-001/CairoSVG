"""
Tests for the ``textLength`` and ``lengthAdjust`` text attributes.

The tests use Cairo font metrics rather than ink (colored pixels) extents:
``textLength`` constrains the typographic advance width, which is independent
of the glyph side bearings.

"""

import io
from math import isclose, isfinite
from pathlib import Path

import cairocffi as cairo
import pytest

from . import parser, surface, svg2png, svg2svg


FONT_ATTR = 'font-family="sans-serif" font-size="20"'


def make_svg(body, width=500, height=200):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}"><rect width="{width}" height="{height}" '
        f'fill="white"/>{body}</svg>')


def layout_for(svg):
    """Build (and render) the SVG, returning the root text layout."""
    tree = parser.Tree(bytestring=svg.encode())
    png_surface = surface.PNGSurface(tree, io.BytesIO(), 96)
    png_surface.finish()
    text_node = next(
        child for child in tree.children if child.tag == 'text')
    return text_node.get('text_length_layout'), text_node


def font_advance(word, font_size=20):
    """Return the natural per-character advances of ``word``."""
    dummy = cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10)
    context = cairo.Context(dummy)
    context.select_font_face('sans-serif')
    context.set_font_size(font_size)
    return [context.text_extents(letter)[4] for letter in word]


def test_missing_attribute_keeps_legacy_layout():
    """Without textLength no layout is built and rendering still works."""
    svg = make_svg(f'<text x="10" y="60" {FONT_ATTR}>Hello</text>')
    layout, _ = layout_for(svg)
    assert layout is None
    assert svg2png(bytestring=svg.encode())[:8] == b'\x89PNG\r\n\x1a\n'


@pytest.mark.parametrize('target', (300, 50))
def test_spacing_matches_target_advance(target):
    """The default spacing mode stretches or compresses advance widths."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="{target}">Hello</text>')
    layout, _ = layout_for(svg)
    total = sum(entry['step'] for entry in layout.entries)
    assert isclose(total, target, rel_tol=1e-9)
    # Glyph outlines are never scaled in spacing mode.
    assert all(entry['glyph_scale'] == 1 for entry in layout.entries)
    natural = font_advance('Hello')
    expected_factor = target / sum(natural)
    for entry, advance in zip(layout.entries, natural):
        assert isclose(entry['step'], advance * expected_factor, rel_tol=1e-9)


def test_spacing_and_glyphs_scales_outlines():
    """spacingAndGlyphs stretches glyph outlines horizontally."""
    target = 300
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="{target}" '
        f'lengthAdjust="spacingAndGlyphs">Hello</text>')
    layout, _ = layout_for(svg)
    natural = sum(font_advance('Hello'))
    expected_scale = target / natural
    assert isclose(
        sum(entry['step'] for entry in layout.entries), target, rel_tol=1e-9)
    for entry in layout.entries:
        assert isclose(entry['glyph_scale'], expected_scale, rel_tol=1e-9)


def test_spacing_and_glyphs_compression():
    """spacingAndGlyphs also shrinks glyph outlines when compressing."""
    target = 30
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="{target}" '
        f'lengthAdjust="spacingAndGlyphs">Hello</text>')
    layout, _ = layout_for(svg)
    assert isclose(
        sum(entry['step'] for entry in layout.entries), target, rel_tol=1e-9)
    assert all(0 < entry['glyph_scale'] < 1 for entry in layout.entries)


def test_length_uses_advance_not_ink_extents():
    """Advance widths come from font metrics, not colored pixels."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="200">Hello</text>')
    layout, _ = layout_for(svg)
    dummy = cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10)
    context = cairo.Context(dummy)
    context.select_font_face('sans-serif')
    context.set_font_size(20)
    for entry in layout.entries:
        metrics = context.text_extents(entry['letter'])
        # The advance width differs from the ink width plus bearing.
        assert entry['advance'] == metrics[4]
        assert entry['advance'] != metrics[2] + metrics[0]


@pytest.mark.parametrize('value,expected', (
    ('100', 100), ('100px', 100), ('5em', 100)))
def test_supported_units(value, expected):
    """Unitless, px and em textLength values are accepted."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="{value}">Hi</text>')
    layout, _ = layout_for(svg)
    assert isclose(
        sum(entry['step'] for entry in layout.entries), expected, rel_tol=1e-9)


@pytest.mark.parametrize('value', ('abc', '10%', '-50', '10pt', '1em 2em'))
def test_invalid_length_falls_back(value):
    """Invalid textLength values keep the legacy plain-text behavior."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="{value}">Hi</text>')
    layout, _ = layout_for(svg)
    assert layout is None


def test_unknown_length_adjust_falls_back():
    """Unknown lengthAdjust values are treated as plain text."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="100" '
        f'lengthAdjust="weird">Hi</text>')
    layout, _ = layout_for(svg)
    assert layout is None


def test_nested_scopes_keep_their_own_target():
    """A child tspan keeps its length; the parent adjusts the other glyphs."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="400">'
        f'A<tspan textLength="60">BC</tspan>DE</text>')
    layout, text_node = layout_for(svg)
    assert isclose(
        sum(entry['step'] for entry in layout.entries), 400, rel_tol=1e-9)
    tspan = text_node.children[0]
    child_scope = layout.scopes[id(tspan)]
    child_length = sum(
        entry['step'] for entry in layout.entries
        if entry['scope'] is child_scope)
    assert isclose(child_length, 60, rel_tol=1e-9)
    # The child glyphs keep the child factor, never the parent factor.
    parent_scope = layout.scopes[id(text_node)]
    assert child_scope.factor != parent_scope.factor
    for entry in layout.entries:
        if entry['scope'] is child_scope:
            assert isclose(entry['glyph_scale'], 1)
    # The glyphs following the tspan resume from the adjusted cursor.
    assert isclose(
        sum(entry['step'] for entry in layout.entries), 400, rel_tol=1e-9)


def test_nested_scope_with_glyph_adjustment():
    """Mixed adjustment modes work inside nested scopes."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="400">'
        f'A<tspan textLength="80" lengthAdjust="spacingAndGlyphs">BC</tspan>'
        f'DE</text>')
    layout, text_node = layout_for(svg)
    tspan = text_node.children[0]
    child_scope = layout.scopes[id(tspan)]
    assert all(
        isclose(entry['glyph_scale'], child_scope.factor)
        for entry in layout.entries if entry['scope'] is child_scope)
    assert isclose(
        sum(entry['step'] for entry in layout.entries), 400, rel_tol=1e-9)


def test_letter_spacing_is_scaled():
    """Existing letter-spacing participates in the adjustment factor."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} letter-spacing="4" '
        f'textLength="200">Hello</text>')
    layout, _ = layout_for(svg)
    assert isclose(
        sum(entry['step'] for entry in layout.entries), 200, rel_tol=1e-9)
    natural = sum(font_advance('Hello')) + 4 * 4
    factor = 200 / natural
    second = layout.entries[1]
    assert isclose(
        second['step'],
        (second['advance'] + second['letter_spacing']) * factor,
        rel_tol=1e-9)


def test_mixed_font_sizes():
    """Nested tspans with different font sizes use their own metrics."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="300">'
        f'A<tspan font-size="40">BC</tspan>DE</text>')
    layout, _ = layout_for(svg)
    assert isclose(
        sum(entry['step'] for entry in layout.entries), 300, rel_tol=1e-9)
    advances = {entry['letter']: entry['advance'] for entry in layout.entries}
    assert advances['B'] > advances['A']


def test_relative_font_size_em():
    """An em font-size on a tspan resolves against the parent font size."""
    from .text import _font_size
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="300">'
        f'A<tspan font-size="2em">BC</tspan>DE</text>')
    layout, text_node = layout_for(svg)
    tspan = text_node.children[0]
    assert _font_size(None, tspan) == 40
    assert isclose(
        sum(entry['step'] for entry in layout.entries), 300, rel_tol=1e-9)


@pytest.mark.parametrize('anchor,shift', (
    ('start', 0), ('middle', -150), ('end', -300)))
def test_text_anchor_on_adjusted_text(anchor, shift):
    """start, middle and end anchors use the adjusted advance width."""
    svg = make_svg(
        f'<text x="100" y="60" {FONT_ATTR} textLength="300" '
        f'text-anchor="{anchor}">Hello</text>')
    layout, _ = layout_for(svg)
    assert isclose(layout.root_anchor_shift, shift, rel_tol=1e-9)


def test_anchor_rendering_position():
    """The first glyph of each anchored line starts at the expected pen x."""
    first_by_baseline = {}
    original_move_to = cairo.Context.move_to
    original_text_path = cairo.Context.text_path
    current = {}

    def move_to(context, x, y):
        current['y'] = y
        return original_move_to(context, x, y)

    def text_path(context, letter):
        if not letter.isspace() and current.get('y') not in first_by_baseline:
            first_by_baseline[current['y']] = context.get_current_point()[0]
        return original_text_path(context, letter)

    svg = make_svg(
        f'<text x="200" y="60" {FONT_ATTR} textLength="300" '
        'text-anchor="start">start</text>'
        f'<text x="200" y="110" {FONT_ATTR} textLength="300" '
        'text-anchor="middle">middle</text>'
        f'<text x="200" y="160" {FONT_ATTR} textLength="300" '
        'text-anchor="end">end</text>')
    cairo.Context.move_to = move_to
    cairo.Context.text_path = text_path
    try:
        surface.PNGSurface(
            parser.Tree(bytestring=svg.encode()), io.BytesIO(), 96).finish()
    finally:
        cairo.Context.move_to = original_move_to
        cairo.Context.text_path = original_text_path
    assert isclose(first_by_baseline[60], 200, abs_tol=1e-6)
    assert isclose(first_by_baseline[110], 50, abs_tol=1e-6)
    assert isclose(first_by_baseline[160], -100, abs_tol=1e-6)

def test_dx_displacement_is_kept():
    """Relative dx offsets still apply on top of the adjusted advances."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} dx="5 10" '
        f'textLength="200">Hello</text>')
    layout, _ = layout_for(svg)
    assert isclose(
        sum(entry['step'] for entry in layout.entries), 200, rel_tol=1e-9)
    svg2png(bytestring=svg.encode())


def test_png_and_vector_use_the_same_layout():
    """PNG and SVG vector surfaces position glyphs from the same advances."""
    points = {}
    original = cairo.Context.text_path

    def spy(context, letter):
        points.setdefault(letter, []).append(context.get_current_point()[0])
        return original(context, letter)

    svg = make_svg(
        f'<text x="30" y="60" {FONT_ATTR} textLength="400">'
        'spacing stretch</text>')

    cairo.Context.text_path = spy
    try:
        surface.PNGSurface(
            parser.Tree(bytestring=svg.encode()), io.BytesIO(), 96).finish()
        png_points = {key: values[:] for key, values in points.items()}
        points.clear()
        surface.SVGSurface(
            parser.Tree(bytestring=svg.encode()), io.BytesIO(), 96).finish()
        vector_points = {key: values[:] for key, values in points.items()}
    finally:
        cairo.Context.text_path = original

    # Both backends follow the same adjusted pen positions; small sub-pixel
    # differences come from the Cairo backends themselves, not from layout.
    assert png_points.keys() == vector_points.keys()
    for letter in png_points:
        for png_x, vector_x in zip(png_points[letter], vector_points[letter]):
            assert isclose(png_x, vector_x, abs_tol=1.5)


@pytest.mark.parametrize('word,target', (
    ('', 100), ('Z', 100), ('Hi', 0), ('Z', 0), ('   ', 100)))
def test_edge_cases_do_not_crash(word, target):
    """Empty text, single glyphs and zero length stay finite."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} '
        f'textLength="{target}">{word}</text>')
    png = svg2png(bytestring=svg.encode())
    vector = svg2svg(bytestring=svg.encode())
    assert png[:8] == b'\x89PNG\r\n\x1a\n'
    assert vector.startswith(b'<?xml')
    layout, _ = layout_for(svg)
    if layout is not None:
        for entry in layout.entries:
            assert entry['step'] >= 0
            assert isfinite(entry['step'])
            assert isfinite(entry['glyph_scale'])


def test_zero_length_glyph_mode_no_invalid_matrix():
    """A zero glyph scale must not build a non-invertible Cairo matrix."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="0" '
        f'lengthAdjust="spacingAndGlyphs">Hi</text>')
    svg2png(bytestring=svg.encode())
    svg2svg(bytestring=svg.encode())


def test_inline_style_declaration():
    """textLength declared through an inline style is honored."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} style="textLength: 150px">Hi</text>')
    layout, _ = layout_for(svg)
    assert isclose(
        sum(entry['step'] for entry in layout.entries), 150, rel_tol=1e-9)


def test_tspan_text_anchor_uses_its_own_length():
    """A scoped tspan with text-anchor shifts by its adjusted chunk length."""
    svg = make_svg(
        f'<text x="100" y="60" {FONT_ATTR} textLength="300">'
        f'A<tspan textLength="100" text-anchor="end">BC</tspan>D</text>')
    layout, text_node = layout_for(svg)
    tspan = text_node.children[0]
    assert isclose(layout.anchor_shifts[id(tspan)], -100, rel_tol=1e-9)
    # The root declares no anchor, so no global shift is applied.
    assert layout.root_anchor_shift == 0


def test_nested_tspan_with_dx_resumes_cursor():
    """Text after a nested scoped tspan continues at the adjusted cursor."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="400">'
        f'A<tspan dx="8" textLength="80">BC</tspan>DE</text>')
    layout, _ = layout_for(svg)
    assert isclose(
        sum(entry['step'] for entry in layout.entries), 400, rel_tol=1e-9)
    # Rendering succeeds on both raster and vector surfaces.
    assert svg2png(bytestring=svg.encode())[:8] == b'\x89PNG\r\n\x1a\n'
    assert svg2svg(bytestring=svg.encode()).startswith(b'<?xml')


REFERENCE_FOLDER = (
    Path(__file__).parent.parent / 'test_non_regression' / 'resources'
    / 'text_length')


@pytest.mark.parametrize('name', (
    'spacing_stretch', 'spacing_compress', 'glyph_stretch',
    'glyph_compress', 'anchors', 'nested'))
def test_rendering_matches_reference_png(name):
    """Raster output matches the committed font-metric reference image."""
    svg_path = REFERENCE_FOLDER / f'{name}.svg'
    reference_path = REFERENCE_FOLDER / f'{name}.png'
    rendered = cairo.ImageSurface.create_from_png(
        io.BytesIO(svg2png(url=str(svg_path))))
    reference = cairo.ImageSurface.create_from_png(str(reference_path))
    assert rendered.get_width() == reference.get_width()
    assert rendered.get_height() == reference.get_height()
    assert rendered.get_data()[:] == reference.get_data()[:]


def test_em_text_length_uses_tspan_font_size():
    """An em textLength on a larger-font tspan resolves against that font."""
    svg = make_svg(
        f'<text x="10" y="60" {FONT_ATTR} textLength="400">'
        f'A<tspan font-size="30" textLength="3em">BC</tspan>DE</text>')
    layout, text_node = layout_for(svg)
    tspan = text_node.children[0]
    scope = layout.scopes[id(tspan)]
    child_length = sum(
        entry['step'] for entry in layout.entries
        if entry['scope'] is scope)
    assert isclose(child_length, 90, rel_tol=1e-9)
    assert isclose(
        sum(entry['step'] for entry in layout.entries), 400, rel_tol=1e-9)


def test_three_level_nested_scopes():
    """Deeply nested scopes each reach their own target length."""
    svg = make_svg(
        f'<text x="10" y="70" {FONT_ATTR} textLength="500">'
        f'AA<tspan textLength="200">BB<tspan textLength="60" '
        f'lengthAdjust="spacingAndGlyphs">CC</tspan>BB</tspan>AA</text>')
    layout, text_node = layout_for(svg)
    targets = {scope.node.text.strip(): scope.target
               for scope in layout.scopes.values()}
    assert targets == {'AA': 500, 'BB': 200, 'CC': 60}
    # Every scope reaches its target through its own glyphs and its nested
    # scopes, and the innermost glyph outlines keep their own scale.
    for scope in layout.scopes.values():
        nested = {entry['scope'] for entry in scope.entries
                  if entry['scope'] is not scope}
        own = sum(entry['step'] for entry in scope.entries
                  if entry['scope'] is scope)
        assert isclose(
            own + sum(nested.target for nested in nested),
            scope.target, rel_tol=1e-9)
    innermost = next(
        scope for scope in layout.scopes.values()
        if scope.node.text.strip() == 'CC')
    assert all(
        isclose(entry['glyph_scale'], innermost.factor)
        for entry in layout.entries if entry['scope'] is innermost)
    # Glyphs of nested scopes are never rescaled by an ancestor.
    for entry in layout.entries:
        assert isfinite(entry['step']) and entry['step'] >= 0
