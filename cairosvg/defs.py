"""
Externally defined elements managers.

This module handles clips, gradients, masks, patterns and external nodes.

"""

import copy
import math
import sys

from .bounding_box import calculate_bounding_box, is_non_empty_bounding_box
from .features import match_features
from .helpers import paint, size, transform
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


def _clone_node(node, parent=None):
    """Copy a node and its children without sharing mutable attributes."""
    clone = copy.copy(node)
    if parent is not None:
        clone.parent = parent
    clone.children = [_clone_node(child, clone) for child in node.children]
    return clone


def _object_bounding_box(surface, node):
    """Get a usable object bounding box for mask geometry."""
    bounding_box = calculate_bounding_box(surface, node)
    if is_non_empty_bounding_box(bounding_box):
        return bounding_box

    x = size(surface, node.get('x'), 'x')
    y = size(surface, node.get('y'), 'y')
    width = size(surface, node.get('width'), 'x')
    height = size(surface, node.get('height'), 'y')
    if width and height:
        return x, y, width, height

    width = surface.context_width or surface.width
    height = surface.context_height or surface.height
    return 0, 0, width, height


def _mask_region(surface, node, mask_node):
    """Return mask region and object bounding box in user coordinates."""
    bounding_box = _object_bounding_box(surface, node)
    if mask_node.get('maskUnits') == 'userSpaceOnUse':
        x = size(surface, mask_node.get('x', '-10%'), 'x')
        y = size(surface, mask_node.get('y', '-10%'), 'y')
        width = size(surface, mask_node.get('width', '120%'), 'x')
        height = size(surface, mask_node.get('height', '120%'), 'y')
    else:
        box_x, box_y, box_width, box_height = bounding_box
        x = box_x + size(surface, mask_node.get('x', '-10%'), 1) * box_width
        y = box_y + size(surface, mask_node.get('y', '-10%'), 1) * box_height
        width = size(
            surface, mask_node.get('width', '120%'), 1) * box_width
        height = size(
            surface, mask_node.get('height', '120%'), 1) * box_height
    return x, y, width, height, bounding_box


def _add_mask_content_transform(mask_node, bounding_box):
    """Apply objectBoundingBox content units to a cloned mask node."""
    if mask_node.get('maskContentUnits') != 'objectBoundingBox':
        return

    x, y, width, height = bounding_box
    content_transform = 'matrix({}, 0, 0, {}, {}, {})'.format(
        width, height, x, y)
    transform_ = mask_node.get('transform')
    if transform_:
        mask_node['transform'] = f'{content_transform} {transform_}'
    else:
        mask_node['transform'] = content_transform


def _mask_surface_scale(surface):
    """Return the current user-to-device scale for both axes."""
    matrix = surface.context.get_matrix()
    scale_x = math.hypot(matrix.xx, matrix.yx)
    scale_y = math.hypot(matrix.xy, matrix.yy)
    if not math.isfinite(scale_x) or scale_x <= 0:
        scale_x = 1
    if not math.isfinite(scale_y) or scale_y <= 0:
        scale_y = 1
    return scale_x, scale_y


def _update_mask_alpha(mask_surface, opacity, mask_type):
    """Apply luminance and opacity to the mask surface alpha channel."""
    if opacity <= 0:
        return

    luminance = mask_type == 'luminance'
    if not luminance and opacity >= 1:
        return

    mask_surface.cairo.flush()
    data = mask_surface.cairo.get_data()
    width = mask_surface.cairo.get_width()
    height = mask_surface.cairo.get_height()
    stride = mask_surface.cairo.get_stride()
    if sys.byteorder == 'little':
        blue, green, red, alpha = 0, 1, 2, 3
    else:
        alpha, red, green, blue = 0, 1, 2, 3

    for row in range(height):
        row_offset = row * stride
        for column in range(width):
            offset = row_offset + column * 4
            alpha_value = data[offset + alpha][0]
            if not alpha_value:
                continue
            if luminance:
                red_value = data[offset + red][0] * 255 / alpha_value
                green_value = data[offset + green][0] * 255 / alpha_value
                blue_value = data[offset + blue][0] * 255 / alpha_value
                luminance_value = (
                    0.2125 * red_value +
                    0.7154 * green_value +
                    0.0721 * blue_value) / 255
            else:
                luminance_value = 1
            new_alpha = int(round(
                alpha_value * luminance_value * opacity))
            new_alpha = max(0, min(255, new_alpha))
            data[offset + alpha:offset + alpha + 1] = bytes((new_alpha,))
    mask_surface.cairo.mark_dirty()


def _paint_typed_mask(surface, node, mask_node, opacity):
    """Paint an explicit alpha or luminance mask on a PNG surface."""
    mask_type = mask_node.get('mask-type', 'alpha').strip().lower()
    x, y, width, height, bounding_box = _mask_region(
        surface, node, mask_node)
    if width <= 0 or height <= 0 or opacity <= 0:
        return

    mask_node = _clone_node(mask_node)
    mask_node.tag = 'g'
    _add_mask_content_transform(mask_node, bounding_box)
    mask_node['width'] = width
    mask_node['height'] = height
    mask_node['viewBox'] = f'{x} {y} {width} {height}'

    scale_x, scale_y = _mask_surface_scale(surface)
    image_width = max(1, int(round(width * scale_x)))
    image_height = max(1, int(round(height * scale_y)))

    from .surface import PNGSurface  # circular import
    mask_surface = PNGSurface(
        mask_node, None, surface.dpi, surface,
        output_width=image_width, output_height=image_height)
    _update_mask_alpha(mask_surface, opacity, mask_type)

    pattern = cairo.SurfacePattern(mask_surface.cairo)
    pattern.set_extend(cairo.EXTEND_NONE)
    pattern.set_matrix(cairo.Matrix(
        image_width / width, 0, 0, image_height / height,
        -x * image_width / width, -y * image_height / height))
    surface.context.mask(pattern)


def _paint_legacy_mask(surface, node, mask_node, opacity):
    """Paint a mask using the historical alpha-based implementation."""
    mask_node = _clone_node(mask_node)
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


def paint_mask(surface, node, name, opacity):
    """Paint the mask of the current surface."""
    mask_node = surface.masks[name]
    mask_type = mask_node.get('mask-type', 'alpha').strip().lower()

    from .surface import PNGSurface  # circular import
    if ('mask-type' in mask_node and
            mask_type in ('alpha', 'luminance') and
            isinstance(surface, PNGSurface)):
        _paint_typed_mask(surface, node, mask_node, opacity)
    else:
        _paint_legacy_mask(surface, node, mask_node, opacity)


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
    pattern_node['opacity'] = float(pattern_node.get('opacity', 1)) * opacity
    pattern_node.tag = 'g'
    transform(surface, pattern_node.get('patternTransform'))

    if pattern_node.get('viewBox'):
        if not (size(surface, pattern_node.get('width', 1), 1) and
                size(surface, pattern_node.get('height', 1), 1)):
            return False
    else:
        if not (size(surface, pattern_node.get('width', 0), 1) and
                size(surface, pattern_node.get('height', 0), 1)):
            return False

    if pattern_node.get('patternUnits') == 'userSpaceOnUse':
        x = size(surface, pattern_node.get('x'), 'x')
        y = size(surface, pattern_node.get('y'), 'y')
        pattern_width = size(surface, pattern_node.get('width', 0), 1)
        pattern_height = size(surface, pattern_node.get('height', 0), 1)
    else:
        _, _, width, height = calculate_bounding_box(surface, node)
        x = size(surface, pattern_node.get('x'), 1) * width
        y = size(surface, pattern_node.get('y'), 1) * height
        pattern_width = (
            size(surface, pattern_node.pop('width', '1'), 1) * width)
        pattern_height = (
            size(surface, pattern_node.pop('height', '1'), 1) * height)
        if 'viewBox' not in pattern_node:
            pattern_node['width'] = pattern_width
            pattern_node['height'] = pattern_height
            if pattern_node.get('patternContentUnits') == 'objectBoundingBox':
                pattern_node['transform'] = f'scale({width}, {height})'

    # Fail if pattern has an invalid size
    if pattern_width == 0.0 or pattern_height == 0.0:
        return False

    from .surface import SVGSurface  # circular import
    pattern_surface = SVGSurface(pattern_node, None, surface.dpi, surface)
    pattern_pattern = cairo.SurfacePattern(pattern_surface.cairo)
    pattern_pattern.set_extend(cairo.EXTEND_REPEAT)
    pattern_pattern.set_matrix(cairo.Matrix(
        pattern_surface.width / pattern_width, 0, 0,
        pattern_surface.height / pattern_height, -x, -y))
    surface.context.set_source(pattern_pattern)
    return True


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
