"""
Externally defined elements managers.

This module handles clips, gradients, masks, patterns and external nodes.

"""

import copy

from .bounding_box import calculate_bounding_box, is_non_empty_bounding_box
from .features import match_features
from .helpers import paint, parse_transform, size, transform
from .parser import Tree
from .shapes import rect
from .surface import cairo
from .url import parse_url

BLEND_OPERATORS = {
    'darken': cairo.OPERATOR_DARKEN,
    'lighten': cairo.OPERATOR_LIGHTEN,
    'multiply': cairo.OPERATOR_MULTIPLY,
    'normal': cairo.OPERATOR_OVER,
    'screen': cairo.OPERATOR_SCREEN,
}

EXTEND_OPERATORS = {
    'none': cairo.EXTEND_NONE,
    'pad': cairo.EXTEND_PAD,
    'reflect': cairo.EXTEND_REFLECT,
    'repeat': cairo.EXTEND_REPEAT,
}

INVISIBLE_DRAW_TAGS = frozenset((
    'clipPath', 'defs', 'filter', 'linearGradient', 'marker', 'mask',
    'pattern', 'radialGradient', 'symbol', 'title', 'desc',
    'metadata', 'style'))


def update_def_href(surface, def_name, def_dict, chain=None):
    """Update the attributes of the def according to its href attribute."""
    def_node = def_dict[def_name]
    href = parse_url(def_node.get_href()).fragment
    if chain is None:
        chain = set()
    if href in def_dict and href not in chain:
        chain.add(href)
        update_def_href(surface, href, def_dict, chain)
        href_node = def_dict[href]
        def_dict[def_name] = Tree(
            url=f'#{def_name}', url_fetcher=def_node.url_fetcher,
            parent=href_node, parent_children=(not def_node.children),
            tree_cache=surface.tree_cache, unsafe=def_node.unsafe)
        # Inherit attributes generally not inherited
        for key, value in href_node.items():
            if key not in def_dict[def_name]:
                def_dict[def_name][key] = value


def parse_all_defs(surface, node):
    """Recursively visit all child nodes and process definition elements."""

    # Handle node
    parse_def(surface, node)

    # Visit all children recursively
    if node.children:
        for child in node.children:
            parse_all_defs(surface, child)


def parse_def(surface, node):
    """Parse the SVG definitions."""
    for def_type in (
            'marker', 'gradient', 'pattern', 'path', 'mask', 'filter',
            'image'):
        if def_type in node.tag.lower() and 'id' in node:
            getattr(surface, f'{def_type}s')[node['id']] = node


def gradient_or_pattern(surface, node, name, opacity):
    """Gradient or pattern color."""
    if name in surface.gradients:
        update_def_href(surface, name, surface.gradients)
        return draw_gradient(surface, node, name, opacity)
    elif name in surface.patterns:
        update_def_href(surface, name, surface.patterns)
        return draw_pattern(surface, node, name, opacity)


def pattern_is_drawing(node):
    """Recursively check that a pattern contains something to draw."""
    for child in node.children:
        if child.tag in INVISIBLE_DRAW_TAGS:
            continue
        if child.children and pattern_is_drawing(child):
            return True
        if not child.children:
            return True
    return False


def marker(surface, node):
    """Store a marker definition."""
    parse_def(surface, node)


def mask(surface, node):
    """Store a mask definition."""
    parse_def(surface, node)


def filter_(surface, node):
    """Store a filter definition."""
    parse_def(surface, node)


def linear_gradient(surface, node):
    """Store a linear gradient definition."""
    parse_def(surface, node)


def radial_gradient(surface, node):
    """Store a radial gradient definition."""
    parse_def(surface, node)


def pattern(surface, node):
    """Store a pattern definition."""
    parse_def(surface, node)


def clip_path(surface, node):
    """Store a clip path definition."""
    if 'id' in node:
        surface.paths[node['id']] = node


def paint_mask(surface, node, name, opacity):
    """Paint the mask of the current surface."""
    mask_node = surface.masks[name]
    mask_node.tag = 'g'
    mask_node['opacity'] = opacity

    if mask_node.get('maskUnits') == 'userSpaceOnUse':
        width_ref, height_ref = 'x', 'y'
    else:
        x = size(surface, node.get('x'), 'x')
        y = size(surface, node.get('y'), 'y')
        width = size(surface, node.get('width'), 'x')
        height = size(surface, node.get('height'), 'y')
        width_ref = width or surface.width
        height_ref = height or surface.height

    mask_node['x'] = size(surface, mask_node.get('x', '-10%'), width_ref)
    mask_node['y'] = size(surface, mask_node.get('y', '-10%'), height_ref)
    mask_node['height'] = size(
        surface, mask_node.get('height', '120%'), height_ref)
    mask_node['width'] = size(
        surface, mask_node.get('width', '120%'), width_ref)

    if mask_node.get('maskUnits') == 'userSpaceOnUse':
        x = mask_node['x']
        y = mask_node['y']
        mask_node['viewBox'] = '{x} {y} {width} {height}'.format(**mask_node)

    from .surface import SVGSurface  # circular import
    mask_surface = SVGSurface(mask_node, None, surface.dpi, surface)
    surface.context.save()
    surface.context.translate(x, y)
    surface.context.scale(
        mask_node['width'] / mask_surface.width,
        mask_node['height'] / mask_surface.height)
    surface.context.mask_surface(mask_surface.cairo)
    surface.context.restore()


def draw_gradient(surface, node, name, opacity):
    """Gradients colors."""
    gradient_node = surface.gradients[name]

    if gradient_node.get('gradientUnits') == 'userSpaceOnUse':
        width_ref, height_ref = 'x', 'y'
        diagonal_ref = 'xy'
    else:
        bounding_box = calculate_bounding_box(surface, node)
        if not is_non_empty_bounding_box(bounding_box):
            return False
        x = size(surface, bounding_box[0], 'x')
        y = size(surface, bounding_box[1], 'y')
        width = size(surface, bounding_box[2], 'x')
        height = size(surface, bounding_box[3], 'y')
        width_ref = height_ref = diagonal_ref = 1

    if gradient_node.tag == 'linearGradient':
        x1 = size(surface, gradient_node.get('x1', '0%'), width_ref)
        x2 = size(surface, gradient_node.get('x2', '100%'), width_ref)
        y1 = size(surface, gradient_node.get('y1', '0%'), height_ref)
        y2 = size(surface, gradient_node.get('y2', '0%'), height_ref)
        gradient_pattern = cairo.LinearGradient(x1, y1, x2, y2)

    elif gradient_node.tag == 'radialGradient':
        r = size(surface, gradient_node.get('r', '50%'), diagonal_ref)
        cx = size(surface, gradient_node.get('cx', '50%'), width_ref)
        cy = size(surface, gradient_node.get('cy', '50%'), height_ref)
        fx = size(surface, gradient_node.get('fx', str(cx)), width_ref)
        fy = size(surface, gradient_node.get('fy', str(cy)), height_ref)
        gradient_pattern = cairo.RadialGradient(fx, fy, 0, cx, cy, r)

    else:
        return False

    # Apply matrix to set coordinate system for gradient
    if gradient_node.get('gradientUnits') != 'userSpaceOnUse':
        gradient_pattern.set_matrix(cairo.Matrix(
            1 / width, 0, 0, 1 / height, - x / width, - y / height))

    # Apply transform of gradient
    transform(
        surface, gradient_node.get('gradientTransform'), gradient_pattern)

    # Apply gradient (<stop> by <stop>)
    offset = 0
    for child in gradient_node.children:
        offset = max(offset, size(surface, child.get('offset'), 1))
        stop_color = surface.map_color(
            child.get('stop-color', 'black'),
            float(child.get('stop-opacity', 1)) * opacity)
        gradient_pattern.add_color_stop_rgba(offset, *stop_color)

    # Set spread method for gradient outside target bounds
    gradient_pattern.set_extend(EXTEND_OPERATORS.get(
        gradient_node.get('spreadMethod', 'pad'), EXTEND_OPERATORS['pad']))

    surface.context.set_source(gradient_pattern)
    return True


def draw_pattern(surface, node, name, opacity):
    """Draw a pattern image."""
    pattern_node = surface.patterns[name]

    # Guard against cyclic references between patterns
    active_patterns = surface.active_patterns
    if name in active_patterns:
        return False

    # An empty pattern paints nothing, the fallback color is used by the
    # caller instead
    if not pattern_is_drawing(pattern_node):
        return False

    bounding_box_units = False
    if pattern_node.get('patternUnits') == 'userSpaceOnUse':
        # Coordinates and lengths are expressed in the current user space;
        # percentages are relative to the current viewport
        x = size(surface, pattern_node.get('x', '0'), 'x')
        y = size(surface, pattern_node.get('y', '0'), 'y')
        pattern_width = size(surface, pattern_node.get('width', '0'), 'x')
        pattern_height = size(surface, pattern_node.get('height', '0'), 'y')
    else:
        # Coordinates and lengths are fractions of the target bounding box
        bounding_box = calculate_bounding_box(surface, node)
        if not is_non_empty_bounding_box(bounding_box):
            return False
        box_x, box_y, box_width, box_height = bounding_box
        x = box_x + size(
            surface, pattern_node.get('x', '0'), 1) * box_width
        y = box_y + size(
            surface, pattern_node.get('y', '0'), 1) * box_height
        pattern_width = size(
            surface, pattern_node.get('width', '1'), 1) * box_width
        pattern_height = size(
            surface, pattern_node.get('height', '1'), 1) * box_height
        bounding_box_units = (
            pattern_node.get('patternContentUnits') == 'objectBoundingBox')

    # Fail if pattern has an invalid (zero or negative-sized) tile
    if pattern_width <= 0.0 or pattern_height <= 0.0:
        return False

    # Never mutate the shared definition: each reference gets a private copy,
    # so opacity is not accumulated and the pattern size is not changed
    # between references
    pattern_node = copy_node(pattern_node)
    pattern_opacity = (
        float(pattern_node.get('opacity', 1)) * opacity)
    pattern_node.tag = 'g'
    # Opacity is applied once on the final cairo pattern, never on the
    # group rendered in the tile surface, where it would be multiplied again
    pattern_node.pop('opacity', None)

    if pattern_node.get('viewBox'):
        # With a viewBox, preserveAspectRatio handles the mapping between
        # the viewBox and the tile: patternContentUnits must be ignored
        pattern_node.pop('patternContentUnits', None)
    else:
        # The tile surface is sized with the resolved tile dimensions:
        # fractions of the bounding box, or viewport-relative percentages,
        # become concrete user-space lengths
        pattern_node['width'] = pattern_width
        pattern_node['height'] = pattern_height
        if bounding_box_units:
            # Pattern contents are expressed in fractions of the
            # bounding box
            old_transform = pattern_node.get('transform')
            content_transform = f'scale({box_width}, {box_height})'
            pattern_node['transform'] = (
                f'{old_transform} {content_transform}'
                if old_transform else content_transform)

    from .surface import SVGSurface  # circular import
    active_patterns.add(name)
    try:
        pattern_surface = SVGSurface(
            pattern_node, None, surface.dpi, surface)
    finally:
        active_patterns.discard(name)
    pattern_pattern = cairo.SurfacePattern(pattern_surface.cairo)
    pattern_pattern.set_extend(cairo.EXTEND_REPEAT)

    # The tile surface is already sized with the resolved tile dimensions,
    # so its user space matches the pattern tile one-to-one: the matrix
    # only shifts the tiling origin to (x, y). Cairo pattern matrices map
    # user space back into tile space, hence the negated translation.
    pattern_matrix = cairo.Matrix(1, 0, 0, 1, -x, -y)

    # Apply patternTransform on top of the tile mapping, without altering
    # the current drawing context. Cairo pattern matrices map user space
    # back into tile space: the user-space patternTransform is therefore
    # inverted before being post-multiplied.
    transform_matrix = parse_transform(
        surface, pattern_node.get('patternTransform'))
    if transform_matrix is not None:
        transform_matrix.invert()
        pattern_matrix = pattern_matrix.multiply(transform_matrix)

    pattern_pattern.set_matrix(pattern_matrix)
    if pattern_opacity < 1:
        # Bake the opacity once into an independent tile copy: painting a
        # semi-transparent pattern references the shared tile surface,
        # which would accumulate the alpha on every reference. An
        # ImageSurface keeps the semantics identical for every output
        # backend while vector backends fall back to an embedded image.
        tile_surface = cairo.ImageSurface(
            cairo.FORMAT_ARGB32,
            max(1, int(round(pattern_surface.width))),
            max(1, int(round(pattern_surface.height))))
        tile_context = cairo.Context(tile_surface)
        tile_context.set_source_surface(pattern_surface.cairo, 0, 0)
        tile_context.paint_with_alpha(pattern_opacity)
        pattern_pattern = cairo.SurfacePattern(tile_surface)
        pattern_pattern.set_extend(cairo.EXTEND_REPEAT)
        pattern_pattern.set_matrix(pattern_matrix)
    surface.context.set_source(pattern_pattern)
    return True


def copy_node(node):
    """Return a private, shallow copy of a shared definition node."""
    node_copy = copy.copy(node)
    # The dict of attributes is copied too: mutating opacity, tag,
    # transform or patternContentUnits never changes the shared node
    return node_copy


def prepare_filter(surface, node, name):
    """Apply a filter transforming the context."""
    if 'id' in node and node['id'] in surface.masks:
        return

    if name in surface.filters:
        filter_node = surface.filters[name]
        for child in filter_node.children:
            # Offset
            if child.tag == 'feOffset':
                if filter_node.get('primitiveUnits') == 'objectBoundingBox':
                    width = size(surface, node.get('width'), 'x')
                    height = size(surface, node.get('height'), 'y')
                    dx = size(surface, child.get('dx', 0), 1) * width
                    dy = size(surface, child.get('dy', 0), 1) * height
                else:
                    dx = size(surface, child.get('dx', 0), 1)
                    dy = size(surface, child.get('dy', 0), 1)
                surface.context.translate(dx, dy)


def apply_filter_before_painting(surface, node, name):
    """Apply a filter transforming the painting operations."""
    if 'id' in node and node['id'] in surface.masks:
        return

    if name in surface.filters:
        filter_node = surface.filters[name]
        for child in filter_node.children:
            # Blend
            if child.tag == 'feBlend':
                surface.context.set_operator(BLEND_OPERATORS.get(
                    child.get('mode', 'normal'), BLEND_OPERATORS['normal']))


def apply_filter_after_painting(surface, node, name):
    """Apply a filter using the painted surface to transform the image."""
    if 'id' in node and node['id'] in surface.masks:
        return

    if name in surface.filters:
        filter_node = surface.filters[name]
        for child in filter_node.children:
            # Flood
            if child.tag == 'feFlood':
                surface.context.save()
                surface.context.new_path()
                if filter_node.get('primitiveUnits') == 'objectBoundingBox':
                    x = size(surface, node.get('x'), 'x')
                    y = size(surface, node.get('y'), 'y')
                    width = size(surface, node.get('width'), 'x')
                    height = size(surface, node.get('height'), 'y')
                else:
                    x, y, width, height = 0, 0, 1, 1
                x += size(surface, child.get('x', 0), 1)
                y += size(surface, child.get('y', 0), 1)
                width *= size(surface, child.get('width', 0), 1)
                height *= size(surface, child.get('height', 0), 1)
                rect(surface, dict(x=x, y=y, width=width, height=height))
                surface.context.set_source_rgba(*surface.map_color(
                    paint(child.get('flood-color'))[1],
                    float(child.get('flood-opacity', 1))))
                surface.context.fill()
                surface.context.restore()


def use(surface, node):
    """Draw the content of another SVG node."""
    surface.reference_count += 1
    if not node.unsafe and surface.reference_count > 100_000:
        raise ValueError(
            'Abort rendering: more than 100 000 referenced elements')
    surface.context.save()
    surface.context.translate(
        size(surface, node.get('x'), 'x'), size(surface, node.get('y'), 'y'))
    if 'x' in node:
        del node['x']
    if 'y' in node:
        del node['y']
    if 'viewBox' in node:
        del node['viewBox']
    if 'mask' in node:
        del node['mask']
    href = parse_url(node.get_href()).geturl()
    try:
        tree = Tree(
            url=href, url_fetcher=node.url_fetcher, parent=node,
            tree_cache=surface.tree_cache, unsafe=node.unsafe)
    except TypeError:
        surface.context.restore()
        return

    if not match_features(tree.xml_tree):
        surface.context.restore()
        return

    if tree.tag in ('svg', 'symbol'):
        # Explicitely specified
        # http://www.w3.org/TR/SVG11/struct.html#UseElement
        tree.tag = 'svg'
        if 'width' in node and 'height' in node:
            tree['width'], tree['height'] = node['width'], node['height']

    surface.draw(tree)
    node.get('fill', None)
    node.get('stroke', None)
    surface.context.restore()
