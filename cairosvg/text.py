"""
Text drawers.

Horizontal ``textLength`` and ``lengthAdjust`` attributes are supported on
``text`` and ``tspan`` elements for left-to-right text:

* lengths are typographic advance widths read from the font metrics, never
  the ink (colored pixels) extents;
* the default ``spacing`` adjustment only changes the inter-glyph advances,
  whereas ``spacingAndGlyphs`` also scales the glyph outlines horizontally;
* a ``tspan`` defining its own ``textLength`` keeps that target length while
  the other glyphs of the parent absorb the remaining adjustment, and the
  following text resumes at the adjusted cursor position;
* ``text-anchor`` (start, middle, end) is computed from the adjusted width;
* unitless, ``px`` and ``em`` lengths are accepted. Missing attributes keep
  the legacy behavior, invalid lengths and unknown adjustment modes fall back
  to plain text, and empty text, single glyphs and zero lengths never produce
  non-finite coordinates.

Vertical text, complex text shaping and text-on-path ``textLength`` are not
handled by this layout.

"""

from math import cos, isfinite, radians, sin
import re

from .bounding_box import (
    EMPTY_BOUNDING_BOX, extend_bounding_box, is_valid_bounding_box)
from .helpers import distance, normalize, point_angle, size, zip_letters
from .surface import cairo
from .url import parse_url


TEXT_LENGTH_RE = re.compile(r'^(\d+\.?\d*|\.\d+)(px|em)?$')


def _declares(node, attribute):
    """Whether ``attribute`` is set on ``node`` itself (not inherited)."""
    if attribute in node.xml_tree.attrib:
        return True
    style = node.xml_tree.get('style', '')
    return f'{attribute.lower()}:' in style.lower()


def _attribute(node, attribute):
    """Return ``attribute`` from ``node``, including lowercased styles."""
    value = node.get(attribute)
    if value is None:
        value = node.get(attribute.lower())
    return value


class _TextLengthScope:
    """A text subtree whose advance width must match ``textLength``.

    A scope is created for every ``text`` or ``tspan`` element defining a
    valid ``textLength`` attribute. Entries claimed by nested scopes are not
    adjusted by the parent scope: they keep their own target length while the
    remaining entries absorb the parent adjustment.

    """
    def __init__(self, node, target, adjust_glyphs):
        self.node = node
        self.target = target
        self.adjust_glyphs = adjust_glyphs
        self.entries = []
        self.parent = None
        self.factor = 1


class _TextLayout:
    """Precomputed horizontal ``textLength`` layout of a ``text`` subtree.

    The layout stores, for every rendered character, the advance width of the
    adjusted step (``step``) and the horizontal scale applied to the glyph
    itself (``glyph_scale``). Lengths are typographic advance widths measured
    with the font metrics, never ink (colored pixels) extents.

    """
    def __init__(self, entries, scopes, root_anchor_shift):
        self.entries = entries
        self.scopes = scopes
        self.root_anchor_shift = root_anchor_shift
        self.anchor_shifts = {}
        self.entries_by_node = {}
        for entry in entries:
            self.entries_by_node.setdefault(id(entry['node']), []).append(
                entry)
        # Number of entries already consumed by each node across the two
        # ``text`` passes (text path then filled/stroked text).
        self._counters = {}

    def next_entry(self, node):
        """Return the next layout entry belonging to ``node``."""
        key = id(node)
        index = self._counters.get(key, 0)
        self._counters[key] = index + 1
        return self.entries_by_node[key][index]

    def has_entry(self, node):
        """Whether ``node`` owns at least one unconsumed layout entry."""
        key = id(node)
        node_entries = self.entries_by_node.get(key)
        return bool(node_entries) and self._counters.get(key, 0) < len(
            node_entries)

    def reset(self):
        """Reset the entry counters before a new rendering pass."""
        self._counters = {}


def _font_size(surface, node):
    """Resolve the font size of ``node``, resolving relative ``em`` units."""
    cached = node.get('text_length_font_size')
    if cached is not None:
        return cached
    parent = node.parent
    parent_size = (
        _font_size(surface, parent) if parent is not None
        else surface.font_size)
    # ``node.get`` inherits the parent font-size, which would resolve an
    # inherited relative unit twice. Only values declared on the node itself
    # are resolved here.
    if not _declares(node, 'font-size'):
        font_size = parent_size
    else:
        value = node.get('font-size')
        old_font_size = surface.font_size
        surface.font_size = parent_size
        try:
            font_size = size(surface, value, 'x')
        finally:
            surface.font_size = old_font_size
        if not (font_size > 0):
            font_size = parent_size
    node['text_length_font_size'] = font_size
    return font_size


def _parse_text_length(surface, node, font_size):
    """Parse ``textLength`` as a user-space length.

    Only unitless lengths, ``px`` and ``em`` are accepted. Return ``None`` for
    missing or invalid values so that callers fall back to plain text layout.

    """
    value = _attribute(node, 'textLength')
    if value is None:
        return None
    match = TEXT_LENGTH_RE.match(normalize(value))
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    if unit == 'em':
        number *= font_size
    if not (number >= 0):
        return None
    return number


def _select_font(surface, node, font_size):
    """Select the font of ``node`` on the Cairo context."""
    font_family = (
        (node.get('font-family') or 'sans-serif').split(',')[0]
        .strip('"\' '))
    font_style = getattr(
        cairo, f'font_slant_{node.get("font-style")}'.upper(),
        cairo.FONT_SLANT_NORMAL)
    node_font_weight = node.get('font-weight')
    if (node_font_weight and node_font_weight.isdigit()
            and int(node_font_weight) >= 550):
        node_font_weight = 'bold'
    font_weight = getattr(
        cairo, (f'font_weight_{node_font_weight}'.upper()),
        cairo.FONT_WEIGHT_NORMAL)
    surface.context.select_font_face(font_family, font_style, font_weight)
    surface.context.set_font_size(font_size)


def _build_text_layout(surface, root):
    """Build the :class:`_TextLayout` of a ``text`` node subtree.

    Return ``None`` when no valid ``textLength`` attribute is found, in which
    case the legacy text layout is used unchanged.

    """
    _font_size(surface, root)

    scopes = {}
    for node in _iter_text_nodes(root):
        # ``node.get`` inherits attributes from ancestors; textLength only
        # opens a scope when declared on the element itself (attribute or
        # inline style).
        if not _declares(node, 'textLength'):
            continue
        font_size = _font_size(surface, node)
        target = _parse_text_length(surface, node, font_size)
        if target is None:
            continue
        if _declares(node, 'lengthAdjust'):
            length_adjust = _attribute(node, 'lengthAdjust')
        else:
            length_adjust = 'spacing'
        if length_adjust not in ('spacing', 'spacingAndGlyphs'):
            continue
        scope = _TextLengthScope(
            node, target, length_adjust == 'spacingAndGlyphs')
        scopes[id(node)] = scope
    if not scopes:
        return None

    entries = []

    def collect(node, scope):
        """Collect the entries of ``node`` and its descendants."""
        if id(node) not in scopes:
            # Nodes without textLength (including anonymous tspans holding
            # tail text) follow the innermost scope currently in effect.
            node_scope = scope
        else:
            node_scope = scopes[id(node)]
        if node_scope is not scope and node_scope is not None:
            node_scope.parent = scope
        font_size = _font_size(surface, node)
        _select_font(surface, node, font_size)
        letter_spacing = size(surface, node.get('letter-spacing'))
        word = node.text or ''
        for index, letter in enumerate(word):
            advance = surface.context.text_extents(letter)[4]
            natural_step = advance
            if index:
                natural_step += letter_spacing
            entry = {
                'letter': letter,
                'node': node,
                'advance': advance,
                'natural_step': natural_step,
                'letter_spacing': letter_spacing if index else 0,
                'scope': node_scope,
                'step': natural_step,
                'glyph_scale': 1,
            }
            entries.append(entry)
            if node_scope is not None:
                # Register the entry on every ancestor scope so that parent
                # targets account for the length claimed by nested scopes.
                ancestor = node_scope
                while ancestor is not None:
                    ancestor.entries.append(entry)
                    ancestor = ancestor.parent
        for child in node.children:
            collect(child, node_scope)

    collect(root, scopes.get(id(root)))

    if not entries:
        return None

    # Resolve scopes from the innermost to the outermost so that entries
    # claimed by a nested scope keep their own target length and are never
    # adjusted twice by their ancestors.
    def depth(scope):
        return 0 if scope.parent is None else depth(scope.parent) + 1

    for scope in sorted(scopes.values(), key=depth, reverse=True):
        own_natural = 0
        nested_target = 0
        nested_scopes = set()
        for entry in scope.entries:
            if entry['scope'] is scope:
                own_natural += entry['natural_step']
            elif entry['scope'] not in nested_scopes:
                nested_scopes.add(entry['scope'])
                nested_target += entry['scope'].target
        # The nested scopes already occupy their own target lengths; the
        # remaining entries absorb the rest of the parent target.
        remaining_target = scope.target - nested_target
        if remaining_target < 0:
            remaining_target = 0
        natural_length = own_natural
        if natural_length > 0:
            factor = remaining_target / natural_length
        else:
            factor = 1
        if not (isfinite(factor) and factor >= 0):
            factor = 1
        scope.factor = factor
        for entry in scope.entries:
            if entry['scope'] is not scope:
                # Claimed by a nested scope: never adjust it twice.
                continue
            glyph_scale = factor if scope.adjust_glyphs else 1
            if not isfinite(glyph_scale):
                glyph_scale = 1
            entry['glyph_scale'] = glyph_scale
            if scope.adjust_glyphs:
                entry['step'] = (
                    entry['advance'] * glyph_scale +
                    entry['letter_spacing'] * factor)
            else:
                # spacing: glyph outlines are untouched, the whole
                # adjustment is carried by the inter-glyph advance.
                entry['step'] = entry['natural_step'] * factor
            if not isfinite(entry['step']) or entry['step'] < 0:
                entry['step'] = 0

    root_anchor_shift = 0
    text_anchor = root.get('text-anchor')
    adjusted_length = sum(entry['step'] for entry in entries)
    if text_anchor == 'middle':
        root_anchor_shift = -adjusted_length / 2
    elif text_anchor == 'end':
        root_anchor_shift = -adjusted_length

    layout = _TextLayout(entries, scopes, root_anchor_shift)
    # text-anchor declared on a scoped tspan shifts its own adjusted chunk.
    for scope in scopes.values():
        node = scope.node
        if node is root or not _declares(node, 'text-anchor'):
            continue
        anchor = _attribute(node, 'text-anchor')
        scope_length = sum(
            entry['step'] for entry in scope.entries
            if entry['scope'] is scope)
        if anchor == 'middle':
            layout.anchor_shifts[id(node)] = -scope_length / 2
        elif anchor == 'end':
            layout.anchor_shifts[id(node)] = -scope_length
    return layout


def _iter_text_nodes(node):
    """Yield ``node`` and all its descendant text nodes."""
    yield node
    for child in node.children:
        yield from _iter_text_nodes(child)


def path_length(path):
    """Get the length of ``path``."""
    total_length = 0
    for item in path:
        if item[0] == cairo.PATH_MOVE_TO:
            old_point = item[1]
        elif item[0] == cairo.PATH_LINE_TO:
            new_point = item[1]
            length = distance(
                old_point[0], old_point[1], new_point[0], new_point[1])
            total_length += length
            old_point = new_point
    return total_length


def point_following_path(path, width):
    """Get the point at ``width`` distance on ``path``."""
    total_length = 0
    for item in path:
        if item[0] == cairo.PATH_MOVE_TO:
            old_point = item[1]
        elif item[0] == cairo.PATH_LINE_TO:
            new_point = item[1]
            length = distance(
                old_point[0], old_point[1], new_point[0], new_point[1])
            total_length += length
            if total_length < width:
                old_point = new_point
            else:
                length -= total_length - width
                angle = point_angle(
                    old_point[0], old_point[1], new_point[0], new_point[1])
                x = cos(angle) * length + old_point[0]
                y = sin(angle) * length + old_point[1]
                return x, y


def text(surface, node, draw_as_text=False):
    """Draw a text ``node``."""
    # Find the root ``text`` element of this node. The textLength layout is
    # built once for the whole subtree and shared by every rendering pass.
    text_root = node
    while text_root.parent is not None and text_root.tag != 'text':
        text_root = text_root.parent
    layout = None
    if text_root.tag == 'text':
        if 'text_length_layout' not in text_root:
            surface.context.save()
            try:
                text_root['text_length_layout'] = _build_text_layout(
                    surface, text_root)
            finally:
                surface.context.restore()
        layout = text_root['text_length_layout']
        if layout is not None and node is text_root:
            # A new rendering pass starts on every call of the root ``text``.
            layout.reset()

    if layout is None:
        node_font_size = surface.font_size
    else:
        node_font_size = _font_size(surface, node)
    _select_font(surface, node, node_font_size)
    ascent, descent, _, max_x_advance, max_y_advance = (
        surface.context.font_extents())

    text_path_href = parse_url(node.get_href() or node.parent.get_href() or '')
    if text_path_href.fragment:
        text_path = surface.paths.get(text_path_href.fragment)
    else:
        text_path = None
    letter_spacing = (
        0 if layout is not None
        else size(surface, node.get('letter-spacing')))
    x_bearing, y_bearing, width, height = (
        surface.context.text_extents(node.text)[:4])

    x, y, dx, dy, rotate = [], [], [], [], [0]
    if 'x' in node:
        x = [size(surface, i, 'x')
             for i in normalize(node['x']).strip().split(' ')]
    if 'y' in node:
        y = [size(surface, i, 'y')
             for i in normalize(node['y']).strip().split(' ')]
    if 'dx' in node:
        dx = [size(surface, i, 'x')
              for i in normalize(node['dx']).strip().split(' ')]
    if 'dy' in node:
        dy = [size(surface, i, 'y')
              for i in normalize(node['dy']).strip().split(' ')]
    if 'rotate' in node:
        rotate = [radians(float(i)) if i else 0
                  for i in normalize(node['rotate']).strip().split(' ')]
    last_r = rotate[-1]
    letters_positions = zip_letters(x, y, dx, dy, rotate, node.text)

    x_align = 0
    y_align = 0

    if layout is not None and node is text_root:
        # Anchor the whole subtree on the adjusted advance width: lengths are
        # typographic advances, so no ink bearing correction is required.
        x_align = layout.root_anchor_shift
    elif layout is not None:
        x_align = layout.anchor_shifts.get(id(node), 0)

    text_anchor = node.get('text-anchor')
    if layout is None and text_anchor == 'middle':
        x_align = - (width / 2 + x_bearing)
        if letter_spacing and node.text:
            x_align -= (len(node.text) - 1) * letter_spacing / 2
    elif layout is None and text_anchor == 'end':
        x_align = - (width + x_bearing)
        if letter_spacing and node.text:
            x_align -= (len(node.text) - 1) * letter_spacing

    # TODO: This is a hack. The rest of the baseline alignment tags of the SVG
    # 1.1 spec (section 10.9.2) are not supported. We only try to align things
    # that look like Western horizontal fonts.
    # Finally, we add a "display-anchor" attribute for aligning the specific
    # text rather than the font baseline.
    # Nonetheless, there are times when one needs to align text vertically, and
    # this will at least make that possible.
    if max_x_advance > 0 and max_y_advance == 0:
        display_anchor = node.get('display-anchor')
        alignment_baseline = (node.get('dominant-baseline') or
                              node.get('alignment-baseline'))
        if display_anchor == 'middle':
            y_align = -height / 2 - y_bearing
        elif display_anchor == 'top':
            y_align = -y_bearing
        elif display_anchor == 'bottom':
            y_align = -height - y_bearing
        elif (alignment_baseline == 'central' or
              alignment_baseline == 'middle'):
            # TODO: This is wrong, Cairo gives no reasonable access to x-height
            # information, so we use font top-to-bottom
            y_align = (ascent + descent) / 2 - descent
        elif (alignment_baseline == 'text-before-edge' or
              alignment_baseline == 'before_edge' or
              alignment_baseline == 'top' or
              alignment_baseline == 'hanging' or
              alignment_baseline == 'text-top'):
            y_align = ascent
        elif (alignment_baseline == 'text-after-edge' or
              alignment_baseline == 'after_edge' or
              alignment_baseline == 'bottom' or
              alignment_baseline == 'text-bottom'):
            y_align = -descent

    bounding_box = EMPTY_BOUNDING_BOX
    if text_path:
        surface.context.new_path()
        surface.stroke_and_fill = False
        surface.draw(text_path)
        surface.stroke_and_fill = True
        cairo_path = surface.context.copy_path_flat()
        surface.context.new_path()
        length = path_length(cairo_path) + x_bearing
        start_offset = size(surface, node.get('startOffset', 0), length)
        if node.tag == 'textPath':
            surface.text_path_width += start_offset
        surface.text_path_width += x_align
        bounding_box = extend_bounding_box(bounding_box, ((start_offset, 0),))

    if node.text:
        for i, ((x, y, dx, dy, r), letter) in enumerate(letters_positions):
            if x:
                surface.cursor_d_position[0] = 0
            if y:
                surface.cursor_d_position[1] = 0
            surface.cursor_d_position[0] += dx or 0
            surface.cursor_d_position[1] += dy or 0
            text_extents = surface.context.text_extents(letter)
            extents = text_extents[4]
            glyph_scale = 1
            if (layout is not None and not text_path and
                    layout.has_entry(node)):
                entry = layout.next_entry(node)
                extents = entry['step']
                glyph_scale = entry['glyph_scale']
            if text_path:
                start = surface.text_path_width + surface.cursor_d_position[0]
                start_point = point_following_path(cairo_path, start)
                middle = start + extents / 2
                middle_point = point_following_path(cairo_path, middle)
                end = start + extents
                end_point = point_following_path(cairo_path, end)
                if i:
                    extents += letter_spacing
                surface.text_path_width += extents
                if not all((start_point, middle_point, end_point)):
                    continue
                if not 0 <= middle <= length:
                    continue
                surface.context.save()
                surface.context.translate(*start_point)
                surface.context.rotate(point_angle(*(start_point + end_point)))
                surface.context.translate(0, surface.cursor_d_position[1])
                surface.context.move_to(0, 0)
                bounding_box = extend_bounding_box(
                    bounding_box, ((end_point[0], text_extents[3]),))
            else:
                surface.context.save()
                x = surface.cursor_position[0] if x is None else x
                y = surface.cursor_position[1] if y is None else y
                if i and layout is None:
                    x += letter_spacing
                surface.context.move_to(x, y)
                cursor_position = x + extents, y
                surface.context.rel_move_to(*surface.cursor_d_position)
                surface.context.rel_move_to(x_align, y_align)
                surface.context.rotate(last_r if r is None else r)
                if isfinite(glyph_scale) and glyph_scale > 0 and (
                        glyph_scale != 1):
                    # Scale only the glyph outlines horizontally; the pen
                    # advance is already given by the adjusted step.
                    surface.context.scale(glyph_scale, 1)
                points = (
                    (cursor_position[0] + x_align +
                     surface.cursor_d_position[0],
                     cursor_position[1] + y_align +
                     surface.cursor_d_position[1]),
                    (cursor_position[0] + x_align +
                     text_extents[4] * glyph_scale +
                     surface.cursor_d_position[0],
                     cursor_position[1] + y_align + text_extents[3] +
                     surface.cursor_d_position[1]))
                bounding_box = extend_bounding_box(bounding_box, points)

            # Only draw characters with 'content' (workaround for bug in cairo)
            if not letter.isspace() and glyph_scale != 0:
                if draw_as_text:
                    surface.context.show_text(letter)
                else:
                    surface.context.text_path(letter)
            surface.context.restore()
            if not text_path:
                surface.cursor_position = cursor_position
    else:
        x = x[0] if x else surface.cursor_position[0]
        y = y[0] if y else surface.cursor_position[1]
        dx = dx[0] if dx else 0
        dy = dy[0] if dy else 0
        surface.cursor_position = (x + dx, y + dy)

    # If a valid bounding box is calculated store it in the node
    if is_valid_bounding_box(bounding_box):
        node['text_bounding_box'] = bounding_box
