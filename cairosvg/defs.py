"""
Externally defined elements managers.

This module handles clips, gradients, masks, patterns and external nodes.

"""

import copy

from .bounding_box import (
    calculate_bounding_box, is_non_empty_bounding_box, is_valid_bounding_box)
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


def copy_node(node, parent=None):
    """Return an independent copy of ``node`` and its children."""
    cloned_node = copy.copy(node)
    cloned_node.parent = parent
    cloned_node.children = [
        copy_node(child, cloned_node) for child in node.children]
    return cloned_node


def mask_geometry(surface, node, mask_node):
    """Return the mask region and content transformation."""
    bounding_box = calculate_bounding_box(surface, node)
    if is_valid_bounding_box(bounding_box):
        bounding_x, bounding_y, bounding_width, bounding_height = bounding_box
    else:
        bounding_x = size(surface, node.get('x'), 'x')
        bounding_y = size(surface, node.get('y'), 'y')
        bounding_width = size(surface, node.get('width'), 'x')
        bounding_height = size(surface, node.get('height'), 'y')

    if mask_node.get('maskUnits', '').lower() == 'userspaceonuse':
        x = size(surface, mask_node.get('x', '-10%'), 'x')
        y = size(surface, mask_node.get('y', '-10%'), 'y')
        width = size(surface, mask_node.get('width', '120%'), 'x')
        height = size(surface, mask_node.get('height', '120%'), 'y')
    else:
        x = bounding_x + size(
            surface, mask_node.get('x', '-10%'), 1) * bounding_width
        y = bounding_y + size(
            surface, mask_node.get('y', '-10%'), 1) * bounding_height
        width = size(
            surface, mask_node.get('width', '120%'), 1) * bounding_width
        height = size(
            surface, mask_node.get('height', '120%'), 1) * bounding_height

    content_units = mask_node.get(
        'maskContentUnits', 'userSpaceOnUse').lower()
    if content_units == 'objectboundingbox':
        content_transform = (
            bounding_x - x, bounding_y - y,
            bounding_width, bounding_height)
    else:
        content_transform = None

    return x, y, width, height, content_transform


def prepare_mask(surface, node, name, opacity):
    """Prepare a mask node and return its geometry."""
    mask_node = copy_node(surface.masks[name])
    geometry = mask_geometry(surface, node, mask_node)
    x, y, width, height, content_transform = geometry
    if width <= 0 or height <= 0:
        return None

    mask_opacity = float(mask_node.get('opacity', 1))
    mask_node.tag = 'g'
    mask_node['opacity'] = mask_opacity * opacity
    mask_node['x'] = 0
    mask_node['y'] = 0
    mask_node['width'] = width
    mask_node['height'] = height
    mask_node.pop('viewBox', None)

    if content_transform:
        translate_x, translate_y, scale_x, scale_y = content_transform
        transform_string = (
            f'translate({translate_x}, {translate_y}) '
            f'scale({scale_x}, {scale_y})')
        mask_node['transform'] = ' '.join(
            part for part in (
                transform_string, mask_node.get('transform')) if part)
    else:
        mask_node['viewBox'] = f'{x} {y} {width} {height}'

    return mask_node, x, y, width, height


def luminance_to_alpha(image_surface):
    """Turn the luminance of an ARGB32 surface into its alpha channel."""
    image_surface.flush()
    width = image_surface.get_width()
    height = image_surface.get_height()
    stride = image_surface.get_stride() // 4
    pixels = memoryview(image_surface.get_data()).cast('I')
    for row in range(height):
        row_start = row * stride
        for column in range(width):
            position = row_start + column
            pixel = pixels[position]
            red = (pixel >> 16) & 0xff
            green = (pixel >> 8) & 0xff
            blue = pixel & 0xff
            luminance = int(
                0.2125 * red + 0.7154 * green + 0.0721 * blue + 0.5)
            pixels[position] = min(luminance, 255) << 24
    image_surface.mark_dirty()


def paint_mask(surface, node, name, opacity):
    """Paint the mask of the current surface."""
    from .surface import PNGSurface, SVGSurface  # circular imports

    prepared_mask = prepare_mask(surface, node, name, opacity)
    if prepared_mask is None:
        return
    mask_node, x, y, width, height = prepared_mask
    mask_type = mask_node.get('mask-type', 'alpha').strip().lower()
    raster = mask_type == 'luminance' and isinstance(surface, PNGSurface)
    surface_class = PNGSurface if raster else SVGSurface
    mask_surface = surface_class(mask_node, None, surface.dpi, surface)
    if raster:
        luminance_to_alpha(mask_surface.cairo)

    surface.context.save()
    surface.context.translate(x, y)
    surface.context.scale(
        mask_node['width'] / mask_surface.width,
        mask_node['height'] / mask_surface.height)
    mask_pattern = cairo.SurfacePattern(mask_surface.cairo)
    mask_pattern.set_extend(cairo.EXTEND_NONE)
    surface.context.mask(mask_pattern)
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
