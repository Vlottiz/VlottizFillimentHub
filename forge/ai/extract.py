"""Pull filament specs out of a web page, PDF, photo or pasted text.

The app does the fetching and cleaning; the model only reads text and fills a
fixed JSON form. Every value must come with a quote from the source, and the
app checks that quote is really there before trusting the value.
"""

from __future__ import annotations

import base64
import ipaddress
import io
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from .. import materials

MAX_SOURCE_CHARS = 16000
MAX_DOWNLOAD = 8_000_000
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# field -> (type, min, max, description shown to the model)
FIELDS: dict[str, tuple] = {
    "brand": ("string", None, None, "Manufacturer / brand name"),
    "product_name": ("string", None, None, "Product name without the brand, e.g. 'PolyTerra PLA'"),
    "material": ("string", None, None, "Material family, e.g. PLA, PLA+, Silk PLA, PETG, ABS, ASA, TPU, PA, PC, PLA-CF"),
    "colour_name": ("string", None, None, "Colour name"),
    "diameter": ("number", 1.5, 3.0, "Filament diameter in mm"),
    "nozzle_min": ("number", 150, 350, "Lowest recommended nozzle/printing/extruder temperature, °C"),
    "nozzle_max": ("number", 150, 350, "Highest recommended nozzle/printing/extruder temperature, °C"),
    "bed_min": ("number", 0, 150, "Lowest recommended bed/build plate temperature, °C"),
    "bed_max": ("number", 0, 150, "Highest recommended bed/build plate temperature, °C"),
    "density": ("number", 0.8, 2.5, "Density in g/cm³"),
    "max_volumetric_speed": ("number", 1, 60, "Max volumetric flow in mm³/s (only if stated)"),
    "max_print_speed": ("number", 10, 1000, "Max or recommended print speed in mm/s"),
    "weight_g": ("number", 50, 10000, "Net filament weight in grams (1 kg = 1000)"),
    "drying": ("string", None, None, "Drying recommendation, e.g. '55 °C for 6 h'"),
    "price": ("number", 0, 10000, "Price"),
    "currency": ("string", None, None, "Currency code, e.g. USD, EUR, GBP"),
}

SYSTEM_PROMPT = (
    "You extract 3D-printer filament specifications from product text.\n"
    "Rules:\n"
    "- Only use facts written in the text. Never fill in typical values from general knowledge.\n"
    "- If something isn't stated, set value and quote to null.\n"
    "- For every value, copy into 'quote' the exact short phrase from the text that states it "
    "(a few words, copied character for character).\n"
    "- A range like '190-230°C' gives min=190 and max=230, both quoting that phrase.\n"
    "- Convert units: temperatures to °C, weight to grams, diameter to mm.\n"
    "- If the text lists several products or variants, use the main product on the page."
)


def schema() -> dict:
    props = {}
    for key, (typ, *_rest) in FIELDS.items():
        props[key] = {
            "type": "object",
            "properties": {"value": {"type": [typ, "null"]}, "quote": {"type": ["string", "null"]}},
            "required": ["value", "quote"],
        }
    return {"type": "object", "properties": props, "required": list(FIELDS)}


def user_prompt(source: str) -> str:
    wanted = "\n".join(f"- {k}: {d}" for k, (_, _, _, d) in FIELDS.items())
    return (f"Find these settings for the filament described below:\n{wanted}\n\n"
            f"TEXT START\n{source}\nTEXT END")


# ------------------------------------------------------------- fetching
class FetchError(Exception):
    pass


def _check_public(url: str) -> None:
    """Refuse URLs pointing at this computer or the local network."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("Enter a full web address starting with http:// or https://")
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except socket.gaierror:
        raise FetchError(f"Couldn't find {u.hostname}. Check the address.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise FetchError("That address points at your own network; only public web pages are allowed.")


def fetch_url(url: str) -> tuple[str, str]:
    """Download a page. Returns (kind, text) where kind is 'html' or 'pdf'."""
    _check_public(url)
    headers = {"User-Agent": UA, "Accept-Language": "en;q=0.9"}
    try:
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=30) as r:
            if r.status_code in (401, 403, 429, 503):
                raise FetchError(f"The site refused the download (HTTP {r.status_code}). Big stores like Amazon "
                                 "often block apps; try the manufacturer's page, a PDF data sheet, a screenshot, "
                                 "or paste the text instead.")
            r.raise_for_status()
            _check_public(str(r.url))  # redirects must stay public too
            data = b""
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > MAX_DOWNLOAD:
                    raise FetchError("That page is too large.")
            ctype = r.headers.get("content-type", "")
    except httpx.HTTPError as e:
        raise FetchError(f"Couldn't download the page: {e}")
    if "pdf" in ctype or data[:5] == b"%PDF-":
        return "pdf", pdf_text(data)
    charset = re.search(r"charset=([\w-]+)", ctype)
    html = data.decode(charset.group(1) if charset else "utf-8", errors="replace")
    return "html", html_text(html)


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "button", "select"}
    BLOCK = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "dt", "dd", "table", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.jsonld: list[str] = []
        self._in_jsonld = False
        self.title = ""
        self._in_title = False
        self.meta: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_jsonld = True
            return
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and (a.get("name") or a.get("property") or "").lower() in (
                "description", "og:title", "og:description", "product:price:amount", "product:price:currency"):
            if a.get("content"):
                self.meta.append(a["content"])
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
        elif tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_jsonld:
            self.jsonld.append(data)
        elif self._in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


def _jsonld_lines(blocks: list[str]) -> list[str]:
    """Keep product facts from structured data (name, brand, price, properties)."""
    out = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            if "Product" in str(o.get("@type", "")):
                for k in ("name", "brand", "color", "material", "description", "weight"):
                    v = o.get(k)
                    if isinstance(v, dict):
                        v = v.get("name") or v.get("value")
                    if v:
                        out.append(f"{k}: {v}")
                offers = o.get("offers")
                for off in offers if isinstance(offers, list) else [offers] if offers else []:
                    if isinstance(off, dict) and off.get("price"):
                        out.append(f"price: {off.get('price')} {off.get('priceCurrency', '')}")
                for p in o.get("additionalProperty") or []:
                    if isinstance(p, dict):
                        out.append(f"{p.get('name')}: {p.get('value')}")
            for v in o.values():
                if isinstance(v, (dict, list)):
                    walk(v)

    for b in blocks:
        try:
            walk(json.loads(b))
        except ValueError:
            continue
    return out


KEYWORDS = re.compile(
    r"°|℃|temp|nozzle|extru|bed|plate|density|g/cm|diameter|mm|speed|dry|weight|kg|price|\$|€|£|"
    r"pla|petg|abs|asa|tpu|nylon|\bpa\b|\bpc\b|colou?r|brand", re.I)


def html_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    body = "".join(p.parts)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip(" |") for ln in body.splitlines()]
    lines = [ln for ln in lines if ln]
    # drop exact duplicate lines (menus repeated for mobile/desktop)
    seen, uniq = set(), []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            uniq.append(ln)
    head = [x for x in [p.title.strip(), *p.meta, *_jsonld_lines(p.jsonld)] if x]
    return shrink("\n".join(head + uniq))


def pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise FetchError("PDF support needs the 'pypdf' package (pip install pypdf).")
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:20])
    except Exception as e:  # pypdf raises many types
        raise FetchError(f"Couldn't read that PDF: {e}")
    if len(text.strip()) < 30:
        raise FetchError("That PDF has no readable text (it may be scanned). Try a photo/screenshot instead.")
    return shrink(text)


def shrink(text: str, limit: int = MAX_SOURCE_CHARS) -> str:
    """Fit long pages into the model's context, keeping spec-like lines first."""
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    keep = set(range(min(15, len(lines))))  # title / intro
    for i, ln in enumerate(lines):
        if KEYWORDS.search(ln):
            keep.update({i - 1, i, i + 1})
    out, size = [], 0
    for i in sorted(k for k in keep if 0 <= k < len(lines)):
        if size + len(lines[i]) > limit:
            break
        out.append(lines[i])
        size += len(lines[i]) + 1
    return "\n".join(out)


# ------------------------------------------------------------ checking
def _norm(s: str) -> str:
    s = s.lower().replace("℃", "°c").replace("º", "°")
    s = re.sub(r"[‐-―−~]", "-", s)
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)
    return re.sub(r"\s+", " ", s).strip()


def _num_tokens(s: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", s)


def _num_str(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def check(raw: dict, source: str | None) -> dict:
    """Validate the model's answer. Returns {field: {value, quote, status, reason}}.

    status: verified    quote found in the source and contains the value
            unverified  plausible, but the quote couldn't be matched (or source was a photo)
            rejected    out of range or not a number
    """
    src = _norm(source) if source else None
    src_words = set(re.findall(r"[a-z0-9.]+", src)) if src else set()
    out = {}
    for key, (typ, lo, hi, _) in FIELDS.items():
        item = raw.get(key)
        if isinstance(item, dict):
            value, quote = item.get("value"), item.get("quote")
        else:
            value, quote = item, None
        if value in (None, "", "null"):
            continue
        res = {"value": value, "quote": quote, "status": "unverified", "reason": None}
        if typ == "number":
            try:
                value = float(str(value).replace(",", "."))
            except ValueError:
                out[key] = {**res, "status": "rejected", "reason": "not a number"}
                continue
            if (lo is not None and value < lo) or (hi is not None and value > hi):
                out[key] = {**res, "value": value, "status": "rejected",
                            "reason": f"outside the sensible range {lo}–{hi}"}
                continue
            res["value"] = int(value) if value.is_integer() else round(value, 3)
        else:
            res["value"] = str(value).strip()[:120]

        if src is None:
            res["reason"] = "read from a photo"
        elif quote:
            q = _norm(str(quote))
            if q and q in src:
                found = True
            else:  # tolerate small paraphrases: all numbers + most words present
                words = re.findall(r"[a-z0-9.]+", q)
                found = bool(words) and all(n in src for n in _num_tokens(q)) and \
                    sum(w in src_words for w in words) / len(words) >= 0.7
            value_in_quote = typ != "number" or _num_str(res["value"]) in _num_tokens(q) or \
                key in ("weight_g", "diameter")  # unit conversions (1 kg -> 1000 g)
            if found and value_in_quote:
                res["status"] = "verified"
            else:
                res["reason"] = "couldn't find the quote on the page" if not found else \
                    "quote doesn't contain this number"
        else:
            if typ == "string" and _norm(res["value"]) in src:
                res["status"] = "verified"
            else:
                res["reason"] = "no quote given"
        out[key] = res

    # sanity across fields
    for a, b in (("nozzle_min", "nozzle_max"), ("bed_min", "bed_max")):
        if a in out and b in out and out[a]["status"] != "rejected" and out[b]["status"] != "rejected":
            if out[a]["value"] > out[b]["value"]:
                out[a]["value"], out[b]["value"] = out[b]["value"], out[a]["value"]
    return out


MATERIAL_ALIASES = [
    (r"pa\d*[- ]?cf|nylon[- ]?cf|carbon.*nylon", "PA-CF"),
    (r"petg[- ]?cf|carbon.*petg", "PETG-CF"),
    (r"pla[- ]?cf|carbon.*pla", "PLA-CF"),
    (r"silk", "PLA Silk"),
    (r"pla\s*(\+|plus|pro)|pla\+|tough pla|pla pro", "PLA+"),
    (r"petg|pet-g|pctg", "PETG"),
    (r"\basa\b", "ASA"),
    (r"\babs\b", "ABS"),
    (r"tpu|tpe|flex", "TPU"),
    (r"nylon|\bpa\d*\b|polyamide", "PA"),
    (r"\bpc\b|polycarbonate", "PC"),
    (r"pla", "PLA"),
]


def guess_material(text: str | None) -> str | None:
    if not text:
        return None
    low = text.lower()
    for name in materials.MATERIALS:
        if low.strip() == name.lower():
            return name
    for pattern, name in MATERIAL_ALIASES:
        if re.search(pattern, low):
            return name
    return None


def to_filament_form(fields: dict) -> dict:
    """Map checked fields onto the Add filament form (skips rejected values)."""
    ok = {k: v["value"] for k, v in fields.items() if v["status"] != "rejected"}
    form: dict = {"spec": {}}
    for src, dst in (("brand", "brand"), ("product_name", "name"), ("colour_name", "colour_name"),
                     ("diameter", "diameter"), ("weight_g", "weight_g"), ("price", "price"),
                     ("currency", "currency")):
        if src in ok:
            form[dst] = ok[src]
    mat = guess_material(ok.get("material")) or guess_material(ok.get("product_name"))
    if mat:
        form["material"] = mat
    for k in ("nozzle_min", "nozzle_max", "bed_min", "bed_max", "density", "max_volumetric_speed"):
        if k in ok:
            form["spec"][k] = ok[k]
    notes = []
    if "drying" in ok:
        notes.append(f"Drying: {ok['drying']}")
    if "max_print_speed" in ok:
        notes.append(f"Maker's max print speed: {ok['max_print_speed']} mm/s")
    if notes:
        form["notes"] = "; ".join(notes)
    return form


# ------------------------------------------------------------- main entry
def extract(chat, *, url: str | None = None, text: str | None = None,
            file_bytes: bytes | None = None, filename: str | None = None) -> dict:
    """Run the whole pipeline. `chat(messages, schema)` returns the model's JSON text."""
    images: list[str] = []
    kind = "text"
    if url:
        kind, source = fetch_url(url)
    elif file_bytes is not None:
        name = (filename or "").lower()
        if name.endswith(".pdf") or file_bytes[:5] == b"%PDF-":
            kind, source = "pdf", pdf_text(file_bytes)
        elif name.endswith((".html", ".htm")):
            kind, source = "html", html_text(file_bytes.decode("utf-8", errors="replace"))
        elif name.endswith((".txt", ".md")):
            kind, source = "text", shrink(file_bytes.decode("utf-8", errors="replace"))
        else:
            kind, source = "image", None
            images = [base64.b64encode(file_bytes).decode()]
    elif text and text.strip():
        if re.search(r"<(html|body|div|table|p)[\s>]", text, re.I):  # pasted page source
            kind, source = "html", html_text(text)
        else:
            source = shrink(text.strip())
    else:
        raise FetchError("Give a link, a file, or some text to read.")

    if source is not None and len(source.strip()) < 40:
        raise FetchError("Hardly any text came back from that page. It may need JavaScript or block apps; "
                         "try a screenshot, the PDF data sheet, or paste the text.")

    if images:
        msg = {"role": "user", "content": user_prompt("(see the attached photo of the label or page)"),
               "images": images}
    else:
        msg = {"role": "user", "content": user_prompt(source)}
    content = chat([{"role": "system", "content": SYSTEM_PROMPT}, msg], schema())
    try:
        raw = json.loads(content)
    except ValueError:
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            raise RuntimeError("The AI didn't return usable data. Try again or use a different source.")
        raw = json.loads(m.group(0))
    fields = check(raw, source)
    return {"kind": kind, "source_chars": len(source or ""), "source_preview": (source or "")[:1500],
            "fields": fields, "form": to_filament_form(fields)}
