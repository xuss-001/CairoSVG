CairoSVG is an SVG converter based on Cairo. It can export SVG files to PDF,
EPS, PS, and PNG files.

* Free software: LGPL license
* For Python 3.10+
* Documentation: https://cairosvg.org/documentation/
* Changelog: https://github.com/Kozea/CairoSVG/releases
* Code, issues, tests: https://github.com/Kozea/CairoSVG
* Code of conduct: https://www.courtbouillon.org/code-of-conduct
* Professional support: https://www.courtbouillon.org
* Donation: https://opencollective.com/courtbouillon

CairoSVG has been created and developed by Kozea (https://kozea.fr).
Professional support, maintenance and community management is provided by
CourtBouillon (https://www.courtbouillon.org).

Non-Scaling Strokes
===================

CairoSVG supports ``vector-effect="non-scaling-stroke"`` on ``path``,
``line``, ``polyline`` and ``polygon`` elements. Path geometry and fill
painting follow the element and ancestor transforms, while the stroke
keeps a constant width in the root SVG viewport coordinate system. This
also applies to dash patterns, line caps and line joins, including with
non-uniform scaling and skew.

Increasing the PNG export scale enlarges the SVG and its non-scaling
strokes together. Existing DPI behavior is unchanged. Text and markers
do not support this vector effect yet.

Copyrights are retained by their contributors, no copyright assignment is
required to contribute to CairoSVG. Unless explicitly stated otherwise, any
contribution intentionally submitted for inclusion is licensed under the LGPL
license, without any additional terms or conditions. For full
authorship information, see the version control history.
