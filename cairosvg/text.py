"""
Text drawers.

"""

import re
from math import cos, isfinite, radians, sin

from .bounding_box import (
    EMPTY_BOUNDING_BOX, extend_bounding_box, is_valid_bounding_box)
from .helpers import distance, normalize, point_angle, size, zip_letters
from .surface import cairo
from .url import parse_url

TEXT_LENGTH_RE = re.compile(
    r'^-?\d+(\.\d+)?(px|em|ex|ch|pt|pc|in|cm|mm|%)?$')


def _select_font(surface, node):
    """Select the font defined by ``node`` on the Cairo context."""
    font_family = (
        (node.get('font-family') or 'sans-serif').split(',')[0].strip('"\' '))
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
    font_size = node.get('font-size')
    surface.context.set_font_size(
        size(surface, font_size) if font_size else surface.font_size)


def _parse_text_length(surface, node):
    """Return the valid ``textLength`` of ``node`` or ``None``."""
    value = node.get('textLength')
    if value is None:
        return None
    value = value.strip()
    # Reject values that are neither a number nor a number followed by a
    # supported length unit; ``size`` would otherwise turn them into 0, which
    # must remain reserved for an explicit ``textLength="0"``.
    if not TEXT_LENGTH_RE.match(value):
        return None
    length = size(surface, value)
    if not isfinite(length) or length < 0:
        return None
    return length


def _length_adjust(node):
    """Return ``'spacing'`` or ``'spacingAndGlyphs'`` for ``node``."""
    value = node.get('lengthAdjust', 'spacing')
    return value if value in ('spacing', 'spacingAndGlyphs') else 'spacing'


def _node_has_text_path(surface, node):
    """Check whether ``node`` or one of its children follows a path."""
    href = parse_url(node.get_href() or '').fragment
    if href and surface.paths.get(href):
        return True
    return any(_node_has_text_path(surface, child) for child in node.children)


def _subtree_has_text_length(node):
    """Check whether a descendant declares a well-formed textLength."""
    if 'textLength' in node.xml_tree.attrib:
        if TEXT_LENGTH_RE.match(node.xml_tree.attrib['textLength'].strip()):
            return True
    return any(
        _subtree_has_text_length(child) for child in node.children)


def _measure_advance(surface, node, letter_spacing):
    """Measure the natural horizontal advance of a leaf text node."""
    text_value = node.text or ''
    if not text_value:
        return 0
    _select_font(surface, node)
    advance = 0
    for index, letter in enumerate(text_value):
        if index:
            advance += letter_spacing
        advance += surface.context.text_extents(letter)[4]
    return advance


def _inside_nested_constraint(index, owner_id, entries, node_entries):
    """Check whether the gap before entry ``index`` belongs to a nested block.

    The gap is excluded when the following character starts a nested
    constraint or when both surrounding characters are inside the same nested
    constraint: such gaps are governed by the nested block, not its ancestor.

    """
    previous_owner = entries[index - 1]['owner']
    current_owner = entries[index]['owner']
    if current_owner != owner_id and current_owner in node_entries:
        return True
    return (
        previous_owner == current_owner and
        previous_owner != owner_id and
        previous_owner in node_entries)


def _build_text_layout(surface, node):
    """Build the ``textLength`` layout of the subtree rooted at ``node``.

    The subtree is flattened in rendering order. Each rendered character gets
    an entry storing the horizontal gap inserted before it (``spacing`` mode)
    and the horizontal scale applied to the glyph and its advance
    (``spacingAndGlyphs`` mode). Characters of a child with its own
    ``textLength`` keep the child adjustment, they are not adjusted again by
    their ancestors.

    """
    entries = []
    node_entries = {}
    constrained = []
    owner_stack = []

    def collect(current):
        """Flatten the subtree, keeping one entry per rendered character."""
        start = len(entries)
        leaf_spacing = size(surface, current.get('letter-spacing'))
        # Only an explicitly declared (not inherited) textLength opens a new
        # constraint scope; the parser otherwise copies the attribute into
        # every descendant node.
        is_constrained = 'textLength' in current.xml_tree.attrib
        if is_constrained and _parse_text_length(surface, current) is None:
            is_constrained = False
        if is_constrained:
            owner_stack.append(id(current))
        if current.text:
            _select_font(surface, current)
            for index, letter in enumerate(current.text):
                glyph_advance = surface.context.text_extents(letter)[4]
                entries.append({
                    'letter': letter,
                    'node_id': id(current),
                    # Gaps already include letter-spacing, matching the
                    # rendering loop that adds it before every character but
                    # the first one.
                    'gap': 0 if index == 0 else leaf_spacing,
                    'scale': 1,
                    'glyph_governed': False,
                    'gap_governed': False,
                    'owner': owner_stack[-1] if owner_stack else None,
                    'natural_advance': glyph_advance,
                })
        for child in current.children:
            collect(child)
        if is_constrained:
            constrained.append((current, start, len(entries), id(current)))
            owner_stack.pop()

    collect(node)

    # Register the constraint ranges before any length computation so that
    # nested blocks can be recognized while processing their ancestors.
    for current, start, end, owner_id in constrained:
        node_entries[owner_id] = {
            'start': start,
            'end': end,
            'target': _parse_text_length(surface, current),
            'natural': 0,
        }

    # First compute the natural length of every constraint, deepest first. A
    # nested constrained subtree is counted as a fixed-length atom using its
    # own target length, so an ancestor never resizes it again.
    for current, start, end, owner_id in reversed(constrained):
        natural_length = 0
        index = start
        while index < end:
            entry = entries[index]
            if index > start:
                natural_length += entry['gap']
            if entry['owner'] != owner_id:
                nested_owner = entry['owner']
                # Deepest constraints are handled first, so the nested
                # constraint metadata is already available.
                if nested_owner in node_entries:
                    nested_info = node_entries[nested_owner]
                    natural_length += nested_info['target']
                    # Gaps inside the nested block are part of its target and
                    # must not be counted again at this level.
                    index = nested_info['end']
                    continue
                else:
                    natural_length += entry['natural_advance']
                    index += 1
                continue
            natural_length += entry['natural_advance']
            index += 1
        node_entries[owner_id]['natural'] = natural_length

    # Then apply the adjustments, deepest nodes first, so characters and gaps
    # already governed by a child keep the child's target length.
    for current, start, end, owner_id in reversed(constrained):
        info = node_entries[owner_id]
        target_length = info['target']
        natural_length = info['natural']
        if natural_length <= 0:
            continue
        if _length_adjust(current) == 'spacingAndGlyphs':
            glyph_scale = target_length / natural_length
            for entry in entries[start:end]:
                # Only glyphs directly owned by this constraint are scaled;
                # glyphs of a nested constrained block keep their own scale.
                if (not entry['glyph_governed'] and
                        entry['owner'] == owner_id):
                    entry['scale'] *= glyph_scale
                    entry['glyph_governed'] = True
        else:
            # Gaps inside a nested constrained block keep that block's
            # adjustment; the other gaps inside the subtree (including the
            # boundaries of nested blocks) absorb this constraint's delta.
            free_gaps = [
                index for index in range(start + 1, end)
                if (not entries[index]['gap_governed'] and
                    not _inside_nested_constraint(
                        index, owner_id, entries, node_entries))]
            if free_gaps:
                delta = (target_length - natural_length) / len(free_gaps)
                for index in free_gaps:
                    entries[index]['gap'] += delta
                    entries[index]['gap_governed'] = True

    return {
        'entries': entries,
        'nodes': node_entries,
        'stroke_index': 0,
        'fill_index': 0,
    }


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
    _select_font(surface, node)
    ascent, descent, _, max_x_advance, max_y_advance = (
        surface.context.font_extents())

    text_path_href = parse_url(node.get_href() or node.parent.get_href() or '')
    if text_path_href.fragment:
        text_path = surface.paths.get(text_path_href.fragment)
    else:
        text_path = None

    # Build the textLength layout once for the whole text subtree. Text laid
    # out on a path keeps the historical behavior and is not adjusted.
    text_layout = None
    if node.tag == 'text':
        cached_layout = getattr(surface, 'text_layout', None)
        if cached_layout is not None and cached_layout.get('root') is node:
            text_layout = cached_layout
        elif (_subtree_has_text_length(node) and
              not _node_has_text_path(surface, node)):
            text_layout = _build_text_layout(surface, node)
            text_layout['root'] = node
            surface.text_layout = text_layout
        else:
            surface.text_layout = None
    else:
        text_layout = getattr(surface, 'text_layout', None)
    # The stroke (text path) and the fill (show text) passes consume the same
    # layout independently; the fill pass restores the cursor saved before
    # the stroke pass and thus starts again from the first entry.
    layout_index_key = 'fill_index' if draw_as_text else 'stroke_index'

    def next_layout_entry(letter):
        """Return the layout entry matching the next rendered character."""
        if text_layout is None:
            return None
        entries = text_layout['entries']
        index = text_layout.get(layout_index_key, 0)
        while index < len(entries) and entries[index]['letter'] != letter:
            index += 1
        if index >= len(entries):
            return None
        text_layout[layout_index_key] = index + 1
        return entries[index]

    def subtree_natural_advance(current):
        advance = 0
        leaf_spacing = size(surface, current.get('letter-spacing'))
        if current.text:
            advance += _measure_advance(surface, current, leaf_spacing)
        for child in current.children:
            advance += subtree_natural_advance(child)
        return advance

    def anchor_width(current):
        """Horizontal advance used for text-anchor positioning."""
        info = text_layout['nodes'].get(id(current)) if text_layout else None
        if info is not None:
            return info['target']
        return subtree_natural_advance(current)

    letter_spacing = size(surface, node.get('letter-spacing'))
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
    anchor_offset_x = 0
    y_align = 0

    text_anchor = node.get('text-anchor')
    if text_layout is None:
        # Historical behavior when no textLength is involved.
        if text_anchor == 'middle':
            x_align = - (width / 2 + x_bearing)
            if letter_spacing and node.text:
                x_align -= (len(node.text) - 1) * letter_spacing / 2
        elif text_anchor == 'end':
            x_align = - (width + x_bearing)
            if letter_spacing and node.text:
                x_align -= (len(node.text) - 1) * letter_spacing
    else:
        anchor_advance = (
            anchor_width(node) if (node.text or node.children) else 0)
        if text_anchor == 'middle':
            anchor_offset_x = -anchor_advance / 2
            if id(node) not in text_layout['nodes']:
                anchor_offset_x -= x_bearing
        elif text_anchor == 'end':
            anchor_offset_x = -anchor_advance
            if id(node) not in text_layout['nodes']:
                anchor_offset_x -= x_bearing
    if text_layout is not None and node.tag == 'text':
        text_layout['anchor_offset_x'] = anchor_offset_x
    elif text_layout is not None:
        # Only the offset defined on the root text element positions the
        # whole line; nested nodes do not shift it again.
        anchor_offset_x = text_layout.get('anchor_offset_x', 0)

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
        surface.text_path_width += (
            x_align if text_layout is None else anchor_offset_x)
        bounding_box = extend_bounding_box(bounding_box, ((start_offset, 0),))

    if node.text:
        anchor_applied = False
        flow_x = None
        for i, ((x, y, dx, dy, r), letter) in enumerate(letters_positions):
            entry_index = (
                text_layout.get(layout_index_key, 0)
                if text_layout is not None else i)
            layout_entry = next_layout_entry(letter)
            glyph_scale = (
                layout_entry['scale'] if layout_entry is not None else 1)
            if layout_entry is not None:
                gap_extra = layout_entry['gap']
            else:
                gap_extra = letter_spacing
            if x:
                surface.cursor_d_position[0] = 0
            if y:
                surface.cursor_d_position[1] = 0
            surface.cursor_d_position[0] += dx or 0
            surface.cursor_d_position[1] += dy or 0
            text_extents = surface.context.text_extents(letter)
            extents = text_extents[4] * glyph_scale
            if text_path:
                start = surface.text_path_width + surface.cursor_d_position[0]
                start_point = point_following_path(cairo_path, start)
                middle = start + extents / 2
                middle_point = point_following_path(cairo_path, middle)
                end = start + extents
                end_point = point_following_path(cairo_path, end)
                if i:
                    extents += gap_extra
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
                if text_layout is None:
                    # Historical positioning without textLength.
                    x = surface.cursor_position[0] if x is None else x
                    y = surface.cursor_position[1] if y is None else y
                    if i:
                        x += letter_spacing
                    surface.context.move_to(x, y)
                    cursor_position = x + text_extents[4], y
                    surface.context.rel_move_to(
                        *surface.cursor_d_position)
                    surface.context.rel_move_to(x_align, y_align)
                    points = (
                        (cursor_position[0] + x_align +
                         surface.cursor_d_position[0],
                         cursor_position[1] + y_align +
                         surface.cursor_d_position[1]),
                        (cursor_position[0] + x_align + text_extents[4] +
                         surface.cursor_d_position[0],
                         cursor_position[1] + y_align + text_extents[3] +
                         surface.cursor_d_position[1]))
                else:
                    if flow_x is None:
                        # First glyph of this node: continue the parent flow.
                        flow_x = surface.cursor_position[0]
                    if x is None:
                        x = flow_x
                    if y is None:
                        y = surface.cursor_position[1]
                    if entry_index == 0 and not anchor_applied:
                        x += anchor_offset_x
                        anchor_applied = True
                    surface.context.move_to(x, y)
                    # Advance the flow past this glyph and across the gap
                    # that precedes the next one. The next glyph may belong to
                    # a child or sibling node reached through the cursor.
                    flow_x = x + extents
                    next_index = text_layout.get(layout_index_key, 0)
                    if next_index < len(text_layout['entries']):
                        flow_x += text_layout['entries'][next_index]['gap']
                    elif i + 1 < len(node.text):
                        flow_x += letter_spacing
                    cursor_position = flow_x, y
                    surface.context.rel_move_to(
                        *surface.cursor_d_position)
                    if glyph_scale > 0 and isfinite(glyph_scale):
                        surface.context.scale(glyph_scale, 1)
                    surface.context.rotate(last_r if r is None else r)
                    points = (
                        (cursor_position[0] +
                         surface.cursor_d_position[0],
                         cursor_position[1] + y_align +
                         surface.cursor_d_position[1]),
                        (cursor_position[0] + extents +
                         surface.cursor_d_position[0],
                         cursor_position[1] + y_align + text_extents[3] +
                         surface.cursor_d_position[1]))
                if text_layout is None:
                    surface.context.rotate(last_r if r is None else r)
                bounding_box = extend_bounding_box(bounding_box, points)

            # Only draw characters with 'content' (workaround for bug in cairo)
            if not letter.isspace():
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
