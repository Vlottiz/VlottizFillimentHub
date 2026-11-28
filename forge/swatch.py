"""Printable QR swatches.

The QR code holds a link to a small page (docs/index.html, served by GitHub
Pages) with the whole profile packed into the link itself, so scanning works
anywhere without this app running. The page rebuilds the Orca preset .json
and offers Download / Share / VlottizFF bundle.

Link format (v1), after '#':
    1*name*group*inherits*printer*<one value per orca.QR_FIELDS_V1>*<sources code>
Each part is percent-encoded with spaces as '+'. Field order is frozen:
printed swatches depend on it.

The model is a .3mf with up to two parts so a toolchanger (like the U1) can
print the QR in a second colour:
    two_colour  body in your filament, QR squares as a flush 0.6 mm inlay part
    hollow      one colour; dark squares cut almost through (deep shadows scan)
"""

from __future__ import annotations

import io
import zipfile
from urllib.parse import quote, unquote_plus

from . import orca

DEFAULT_BASE_URL = "https://vlottiz.github.io/VlottizFillimentHub/"
QUIET = 2          # light border around the code, in modules
INLAY_DEPTH = 0.6  # two-colour: thickness of the coloured QR layer (mm)
HOLLOW_FLOOR = 0.8  # hollow: plastic left under the dark squares (mm)


# ----------------------------------------------------------------- payload
def _enc(s: str) -> str:
    # Characters allowed as-is after '#' in a URL stay readable (and short).
    # '*' (separator), '+' (space), '%' and '#' are always encoded.
    return quote(str(s), safe=" @():/,;!$'=").replace(" ", "+")


def encode(name: str, group: str, inherits: str, printer: str, settings: dict, sources: dict) -> str:
    vals = ["" if settings.get(k) in (None, "") else orca._fmt(settings[k]) for k in orca.QR_FIELDS_V1]
    parts = ["1", name, group, inherits or "", printer or "", *vals, orca.sources_code(settings, sources)]
    return "*".join(_enc(p) for p in parts)


def decode(fragment: str) -> dict:
    """Python twin of docs/ff.js decode(); used by the importer and tests."""
    parts = [unquote_plus(p) for p in fragment.lstrip("#").split("*")]
    if not parts or parts[0] != "1":
        raise ValueError("Not a Filament Forge QR link (or a newer version)")
    n = len(orca.QR_FIELDS_V1)
    if len(parts) != 5 + n + 1:
        raise ValueError("QR link is incomplete")
    name, group, inherits, printer = parts[1:5]
    vals = parts[5:5 + n]
    settings = {k: orca._parse_scalar(v) for k, v in zip(orca.QR_FIELDS_V1, vals) if v != ""}
    settings = {k: v for k, v in settings.items() if v is not None}
    sources = orca.read_sources_tag(f"[ff1:{parts[-1]}]", settings)
    return {"name": name, "group": group, "inherits": inherits, "printer": printer,
            "settings": settings, "sources": sources}


def link(base_url: str, payload: str) -> str:
    base = base_url.strip() or DEFAULT_BASE_URL
    if not base.endswith(("/", ".html")):
        base += "/"
    return f"{base}#{payload}"


def qr_matrix(url: str) -> list[list[bool]]:
    import segno  # imported lazily so the rest of the app works without it
    q = segno.make(url, error="m", micro=False)
    return [[bool(c) for c in row] for row in q.matrix_iter(scale=1, border=0)]


def qr_svg(url: str) -> str:
    import segno
    buf = io.BytesIO()
    segno.make(url, error="m", micro=False).save(buf, kind="svg", scale=4, border=QUIET, dark="#111", light="#fff")
    return buf.getvalue().decode()


# ------------------------------------------------------------------- mesh
class _Mesh:
    def __init__(self):
        self.verts: list[tuple[float, float, float]] = []
        self.index: dict[tuple, int] = {}
        self.tris: list[tuple[int, int, int]] = []

    def v(self, x, y, z) -> int:
        key = (round(x, 5), round(y, 5), round(z, 5))
        i = self.index.get(key)
        if i is None:
            i = self.index[key] = len(self.verts)
            self.verts.append(key)
        return i

    def quad(self, a, b, c, d):
        """Counter-clockwise a-b-c-d seen from outside."""
        a, b, c, d = (self.v(*p) for p in (a, b, c, d))
        self.tris += [(a, b, c), (a, c, d)]


def _heightfield(heights: list[list[float]], cell: float, base: float = 0.0) -> _Mesh:
    """Solid from per-cell top heights (0 = empty), bottom at `base`.

    Built on a shared vertex grid so neighbouring cells share edges and the
    result is a closed surface.
    """
    m = _Mesh()
    rows, cols = len(heights), len(heights[0])

    def h(r, c):
        return heights[r][c] if 0 <= r < rows and 0 <= c < cols else 0.0

    for r in range(rows):
        for c in range(cols):
            top = h(r, c)
            if top <= base:
                continue
            # rows go down the code; flip so row 0 is at the back (max y)
            x0, x1 = c * cell, (c + 1) * cell
            y1, y0 = (rows - r) * cell, (rows - r - 1) * cell
            m.quad((x0, y0, top), (x1, y0, top), (x1, y1, top), (x0, y1, top))          # top
            m.quad((x0, y0, base), (x0, y1, base), (x1, y1, base), (x1, y0, base))      # bottom
            # walls where the neighbour is lower: (dr, dc, edge start, edge end) seen from outside
            for dr, dc, p, q in ((1, 0, (x0, y0), (x1, y0)), (-1, 0, (x1, y1), (x0, y1)),
                                 (0, -1, (x0, y1), (x0, y0)), (0, 1, (x1, y0), (x1, y1))):
                low = max(h(r + dr, c + dc), base)
                if low < top:
                    m.quad((p[0], p[1], low), (q[0], q[1], low), (q[0], q[1], top), (p[0], p[1], top))
    return m


def build_parts(matrix: list[list[bool]], size_mm: float = 60.0, thickness: float = 5.0,
                style: str = "two_colour") -> tuple[list[tuple[str, _Mesh]], float]:
    """Return ([(part name, mesh)], module size in mm)."""
    n = len(matrix)
    total = n + 2 * QUIET
    cell = size_mm / total
    dark = [[False] * total for _ in range(total)]
    for r, row in enumerate(matrix):
        for c, val in enumerate(row):
            dark[r + QUIET][c + QUIET] = val

    if style == "hollow":
        floor = min(HOLLOW_FLOOR, thickness - 0.4)
        heights = [[floor if d else thickness for d in row] for row in dark]
        return [("Swatch", _heightfield(heights, cell))], cell

    if style != "two_colour":
        raise ValueError("style must be two_colour or hollow")
    pocket = thickness - INLAY_DEPTH
    body = _heightfield([[pocket if d else thickness for d in row] for row in dark], cell)
    qr = _heightfield([[thickness if d else 0.0 for d in row] for row in dark], cell, base=pocket)
    return [("Swatch", body), ("QR code (second colour)", qr)], cell


# -------------------------------------------------------------------- 3MF
def to_3mf(parts: list[tuple[str, _Mesh]], title: str) -> bytes:
    """One object made of parts, so Orca lets you pick a filament per part."""
    esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))
    objs = []
    for i, (name, mesh) in enumerate(parts, start=1):
        verts = "".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in mesh.verts)
        tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.tris)
        objs.append(f'<object id="{i}" name="{esc(name)}" type="model"><mesh><vertices>{verts}</vertices>'
                    f'<triangles>{tris}</triangles></mesh></object>')
    comp_id = len(parts) + 1
    comps = "".join(f'<component objectid="{i}"/>' for i in range(1, len(parts) + 1))
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<metadata name="Title">{esc(title)}</metadata><metadata name="Application">Filament Forge</metadata>'
             f'<resources>{"".join(objs)}<object id="{comp_id}" name="{esc(title)}" type="model">'
             f'<components>{comps}</components></object></resources>'
             f'<build><item objectid="{comp_id}"/></build></model>')
    content_types = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                     '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
    return buf.getvalue()
