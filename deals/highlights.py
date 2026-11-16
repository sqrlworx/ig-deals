"""Pull 2-3 short "why it's great" bullets for a product, from the brand's own copy.

Heuristic, stdlib only. Two sources, merged and scored:
  * the Shopify description (body_html): authored <li> bullets, spec rows ("Blade Material: Nitro-V"),
    and feature-bearing sentences/clauses from the prose
  * the product page (only for items that make a post): text segments that are NOT on the homepage
    (so nav/footer/promo bars cancel out), up to where related products / reviews begin. Many themes
    keep their feature callouts ("Holds 1-12 Cards", "RFID-Blocking Plates") only there.
Candidates are cleaned, length-capped, scored and de-duplicated. Anything off can be overridden per
product with `highlights` in the category config.
"""

from __future__ import annotations

import html
import re

MAX_LEN = 60  # ~1 line at 30px in the 920px slide column
MIN_LEN = 10

FEATURE_RE = re.compile(
    r"pocket|compartment|sleeve|zip|magnet|water|weather|rain|recycled|laptop|tablet|\d+\s?l\b|liters?|litres?|"
    r"fits|holds|strap|harness|hip ?belt|blade|steel|titanium|alumin|carbon|leather|nylon|cordura|x-?pac|"
    r"dyneema|ripstop|ballistic|lightweight|ultralight|\d\s?oz\b|\d\s?g\b|rfid|warranty|guarantee|lifetime|"
    r"padded|organi[sz]|quick.access|clamshell|expand|convert|lock|clip|pivot|bearing|flipper|thumb ?stud|"
    r"tsa|airline|carry-?on|overhead|\bcards?\b|coins?|cash|\bkeys?\b|charger|cable|magsafe|airtag|bottle|"
    r"lumens|battery|usb|rechargeable|made in|handmade|hand-?made|slim|compact|durable|abrasion|ykk|"
    r"hypalon|buckle|load lifter|frame|crossbody|shoulder|backpack|sling|tote|wallet|pouch|holster|sheath|"
    r"g-?10|micarta|s35vn|\bd2\b|14c28n|m390|20cv|magnacut|nitro-?v|9cr18mov|patent|dwr|lining|divider|"
    r"tripod|camera|passport|velcro|molle|daisy|webbing|handle|sternum|ergonomic|breathab|reflective|vegan|"
    r"holder|mesh|panel|ways to carry|expansion|adjustab|anti-?theft|secure|hidden|stash|detachable|removable",
    re.I,
)
SKIP_RE = re.compile(
    r"seconds|blemish|cosmetic|final sale|no returns?|returns?\b|exchange|ship|deliver|sample room|"
    r"limited (?:quantit|stock)|while supplies|discount|% ?off|\bsale\b|\bprice|\$|warranty claim|"
    r"\bcare\b|wash|clean|imported|model (?:number|name)|\bsku\b|packing|package|size chart|please|"
    r"\bnote\b|disclaimer|click|learn more|shop\b|\bbuy\b|subscribe|©|https?:|www\.|\bjoin\b|"
    r"pre-?order|back ?order|restock|collab|limited edition|snoopy|peanuts|disney|mickey|marvel|batman|"
    r"prop 65|california|cancer|reproductive|not (?:included|intended)|sold separately|"
    r"\bi\b|\bi'm\b|\bmy\b|\bme\b|\blove\b|\breview|\bstars?\b|klarna|afterpay|affirm|in stock|"
    r"add to cart|checkout|quantity|reward|points|\bfaq\b|questions?|\?|refund|zoom|share this|back to\b|"
    r"map guarantee|price match|repair yourself|features? (?:&|and) specs|style=|[{}<>=]|age range|"
    r"bought for|verified|helpful|was this|wishlist|size guide|what'?s included|in the box|"
    r"^(?:specs|specifications|details|overview|features|description|materials?)$|sold out|unavailable|"
    r"not available|inventory|modal|product image|payment|security|repairs? program|notify me|"
    r"\d\S*\s?x\s?\d|\bin stock\b|pick ?up|gift|engrav|personali[sz]|required|place of purchase|"
    r"registration|\S /.+ / \S|repair",
    re.I,
)
# spec key (lowercase prefix) -> phrasing; earlier = more useful on a slide
SPEC_KEYS = [
    ("blade steel", "{v} blade steel"), ("blade material", "{v} blade steel"),
    ("locking mechanism", "{v}"), ("lock type", "{v}"), ("pivot", "{v} pivot"),
    ("handle material", "{v} handle"), ("capacity", "{v} capacity"), ("volume", "{v} capacity"),
    ("laptop", "Fits up to {v} laptops"), ("material", "{v}"), ("fabric", "{v}"),
    ("hardware", "{v}"), ("blade length", "{v} blade"), ("knife weight", "Weighs {v}"),
    ("weight", "Weighs {v}"), ("warranty", "{v} warranty"), ("steel", "{v} steel"),
]
SPEC_SKIP = re.compile(r"model|number|dimension|overall|closed|width|height|thickness|packing|package|"
                       r"after|upc|sku|country|color|colour|finish|designer|hardness|grind|type|liner|length|"
                       r"spacer|screw|clip|size", re.I)
# where "related products" / reviews start on a product page
PAGE_STOP_RE = re.compile(r"you may also like|you might also like|related products|recommended|"
                          r"pairs? (?:well|perfectly)|complete the (?:look|set)|customers also|"
                          r"customer reviews|^reviews$|what (?:people|customers) (?:are saying|say)|recently viewed|"
                          r"frequently bought|shipping and returns", re.I)
_LEAD_STRIP = re.compile(
    r"^(?:(?:the|this|our|its?|each)\s+(?:[\w®™'’()-]+\s+){0,5}?)?"
    r"(?:features?|includes?|has|have|offers?|boasts?|comes? with|is backed by|are backed by|adds?|uses?|"
    r"is made (?:from|of|with)|is crafted from|is built (?:from|with))\s+(?:an?\s+|the\s+|our\s+)?",
    re.I)
_BAD_START = re.compile(r"^(?:or|yet|while|whether|if|using|so|but|which|that|then|when|where|because|as|"
                        r"than|till|until|making|giving|from|to|in|on|at|by|of|for|go|pick|whatever|even|"
                        r"also|just|plus it|it's|this is|here's|what|why|how|meet|introducing|more)\b", re.I)
_BAD_END = re.compile(r"\b(?:a|an|the|of|for|to|with|by|our|your|is|are|and|or|its|that|this|stylish|"
                      r"beautiful|durable|lightweight|fast|versatile|perfect|ideal|mighty|essential|"
                      r"compact|sleek|simple|minimal|minimalist|premium|functional)$", re.I)
_SELF_DESC = re.compile(r"^(?:the|this|our|it)\b[^,]{0,50}\b(?:is|are)\s+(?:an?\s+|the\s+|our\s+)?"
                        r"(?:\w+\s+){0,1}\w+$", re.I)

_BLOCK = r"p|div|li|ul|ol|h[1-6]|section|tr|td|th|table|dt|dd|blockquote"


def _text(h: str) -> str:
    h = html.unescape(re.sub(r"(?s)<[^>]+>", " ", h))
    return re.sub(r"\s+", " ", h).strip()


def _metric_trim(v: str) -> str:
    """'5.38oz / 152.6g' -> '5.38oz'; 'Up to 16” laptop' -> '16”'."""
    v = re.split(r"\s*/\s*(?=\d)", v)[0]
    v = re.sub(r"\s*\(.*?\)", "", v)
    v = re.sub(r"^(?:up to|approx\.?|approximately)\s+", "", v, flags=re.I)
    v = re.sub(r"\s+(?:laptops?|liters?)$", "", v, flags=re.I) if re.match(r"^[\d.]+", v) else v
    return v.strip(" .;,")


def _tidy(s: str, fragment: bool = False) -> str:
    s = re.sub(r"[\U000E0000-\U000E007F\u200b-\u200f\u2060\ufeff\u00ad]", "", s)  # invisible chars
    s = re.sub(r"\s*[®™©]", "", s)
    if len(s) > 6 and s.isupper():  # "ULTRA-SLIM BIFOLD FOR CASH" -> "Ultra-slim bifold for cash"
        s = s.lower()
    s = re.sub(r"\s+", " ", s).strip(" -–—•·*✓✔️|:;,.")
    s = re.sub(r"\s*\(\d+\)", "", s)  # "One (1) large pocket"
    s = re.sub(r"^(?:now\s+)?(?:and|with|plus|featuring|including|includes?)\s+", "", s, flags=re.I)
    if len(s) > 24:  # "Features include an internal pocket" -> "Internal pocket"
        s = _LEAD_STRIP.sub("", s)
        s = re.sub(r"^(?:includes?|including|with)\s+", "", s, flags=re.I)
    s = re.sub(r"^(?:an?\s+)" + (r"|^the\s+" if fragment else ""), "", s, flags=re.I)
    s = re.sub(r"\b(\w+)\s+\1\b", r"\1", s, flags=re.I)  # "Liner Lock lock"
    return s[:1].upper() + s[1:] if s else s


def _ok(s: str) -> bool:
    return bool(s) and MIN_LEN <= len(s) <= MAX_LEN and not _BAD_START.search(s) \
        and not _BAD_END.search(s) and not _SELF_DESC.search(s)


def _shorten(s: str) -> str | None:
    """Fit a sentence under MAX_LEN, else take its most feature-dense clause; None if nothing fits."""
    full = _tidy(s)
    if _ok(full):
        return full
    parts = re.split(r";\s+|\s+[–—-]\s+|,\s+(?=(?:and|with|while|so|which|plus|giving|keeping|letting|"
                     r"making|perfect|ideal|built|designed|offering|featuring|including)\b)|"
                     r"\s+(?:while|so that|which|because|whether)\s+|\.\s+", s)
    clauses = [_tidy(c, fragment=True) for c in parts]
    clauses = [c for c in clauses if _ok(c) and FEATURE_RE.search(c)]
    if not clauses:
        return None
    return max(clauses, key=lambda c: (min(len(FEATURE_RE.findall(c)), 3), -abs(len(c) - 40)))


def _spec(line: str) -> tuple[str | None, int]:
    """'Blade Material: Nitro-V' -> ('Nitro-V blade steel', priority). Unknown keys -> (None, -1)."""
    m = re.match(r"^\s*([A-Za-z][A-Za-z /&()-]{1,30}?)\s*[:：]\s*(.{1,80})$", line)
    if not m:
        return None, -1
    k, raw = m.group(1).strip().lower(), m.group(2).strip()
    if SPEC_SKIP.search(k):
        return None, 99
    for i, (key, fmt) in enumerate(SPEC_KEYS):
        if key in k:
            v = _metric_trim(raw.split("|")[0])
            suffix = fmt.replace("{v}", "").strip().lower()
            if (len(v.split()) > 4 or re.match(r"(?i)fits|holds|carr|secures|stores|keeps|takes", v)
                    or (suffix and set(suffix.split()) & set(v.lower().split()))):
                fmt = "{v}"  # value is already a phrase: "Holds 2-6 keys", "14C28N steel blade"
            out = _tidy(fmt.format(v=v))
            return (out if MIN_LEN - 4 <= len(out) <= MAX_LEN else None), i
    # "RFID-Blocking: Protects against wireless theft" -> the label is the feature
    if 1 <= len(k.split()) <= 5 and len(raw) > 20 and FEATURE_RE.search(m.group(1)):
        return _tidy(m.group(1)), 50
    return None, 99


def _split_spec_runs(text: str) -> str:
    """CIVIVI-style specs glued together: 'Nitro-VBlade Hardness: 59' -> newline before each key."""
    if text.count(":") < 3:
        return text
    return re.sub(r"(?<=[a-z0-9\")])(?=[A-Z][a-z]+(?:[ /][A-Za-z()]+){0,4}\s?:\s)", "\n", text)


def _norm(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


class _Pool:
    def __init__(self):
        self.c: list[tuple[float, int, str]] = []

    def add(self, text: str | None, base: float):
        if not text or SKIP_RE.search(text) or not (MIN_LEN - 4 <= len(text) <= MAX_LEN):
            return
        words = re.findall(r"[A-Za-z]{2,}", text)
        if len(words) < 2 or not any(len(w) >= 4 for w in words):  # "1.74lbs (0.79kg)", "Sheath"
            return
        if re.search(r":\s*[\d.]+\s*(?:\"|”|″|mm|cm|in\b)", text):  # raw dimension rows
            return
        feats = len(FEATURE_RE.findall(text))
        if base < 1.5 and feats == 0:  # prose must name a concrete feature
            return
        score = base + min(feats, 3) * 0.45 + (0.4 if 16 <= len(text) <= 46 else 0)
        score -= 0.5 if re.search(r"\byou(?:r)?\b", text, re.I) else 0
        self.c.append((score, len(self.c), text))

    def add_line(self, line: str, li_base: float, prose_base: float):
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            return
        sp, pri = _spec(line)
        if sp:
            self.add(sp, 2.6 - pri * 0.06 if pri < 50 else 1.9)
            return
        if pri == 99 and len(line) < 90:  # a spec row we deliberately don't phrase
            return
        if len(line) <= MAX_LEN and not re.search(r"[.!]\s+\S", line):  # already bullet-sized
            self.add(_tidy(line) if _ok(_tidy(line)) else None, li_base)
            return
        for sent in re.split(r"(?<=[.!])\s+(?=[A-Z])", line):
            self.add(_shorten(sent), prose_base)

    def pick(self, title: str, limit: int, other_titles: set[str] = frozenset(),
             variant_strs: set[str] = frozenset()) -> list[str]:
        title_words = _norm(title)
        lead = " ".join(re.findall(r"[a-z0-9]+", title.lower())[:2])
        out: list[str] = []
        for _, _, text in sorted(self.c, key=lambda c: (-c[0], c[1])):
            low = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
            if lead and low.startswith(lead):  # "The Kadet 9L is ...", "CARRYALL Duffel 40L - more space"
                continue
            if any(low.startswith(t) or (len(low) >= 8 and t.startswith(low))
                   for t in other_titles):  # related-product tiles on the page
                continue
            if re.match(r"^[A-Z]{4,}(?: [\w-]+){1,4} \d+(?:\.\d+)?L$", text):  # "MOUNTAIN Hip Pack 3.5L" tile
                continue
            if low in variant_strs or any(v in low for v in variant_strs if " " in v):  # variant picker
                continue
            words = _norm(text)
            if not words or len(words & title_words) / len(words) > 0.7:  # just repeats the name
                continue
            if any(len(words & _norm(o)) / max(1, min(len(words), len(_norm(o)))) >= 0.5 for o in out):
                continue
            out.append(text)
            if len(out) >= limit:
                break
        return out


def body_lines(body_html: str | None) -> tuple[list[str], list[str]]:
    """(li_items, other_lines) from a product description."""
    if not body_html:
        return [], []
    h = re.sub(r"(?is)<(script|style|iframe|video)\b.*?</\1>", " ", body_html)
    lis = []
    for li in re.findall(r"(?is)<li\b[^>]*>(.*?)</li>", h):
        # "<strong>Easy Gear Storage</strong> Large external zippered pocket" -> keep the explanation
        m = re.match(r"(?is)\s*<(strong|b)\b[^>]*>(.*?)</\1>\s*(.+)", li)
        lis.append(_text(m.group(3)) if m and len(_text(m.group(3))) > 12 else _text(li))
    rest = re.sub(r"(?is)<li\b[^>]*>.*?</li>", "\n", h)
    rest = re.sub(rf"(?is)</?(?:{_BLOCK})\b[^>]*>|<br\b[^>]*>", "\n", rest)
    rest = html.unescape(re.sub(r"(?s)<[^>]+>", " ", rest))
    return lis, [x for line in rest.split("\n") for x in _split_spec_runs(line).split("\n")]


def page_lines(segments: list[str], home: set[str], title: str, cap: int = 60) -> list[str]:
    """Product-page text segments that aren't site chrome, from the product name until related/reviews."""
    lead = " ".join(re.findall(r"[a-z0-9]+", title.lower())[:2])
    segs = [s for s in segments if s.lower() not in home]
    start = next((i + 1 for i, s in enumerate(segs)
                  if len(s) < 90 and " ".join(re.findall(r"[a-z0-9]+", s.lower())).startswith(lead)), 0)
    out = []
    for s in segs[start:]:
        if PAGE_STOP_RE.search(s) and len(s) < 60:
            break
        out.append(s)
        if len(out) >= cap:
            break
    return out


def _variant_strs(product: dict | None) -> set[str]:
    out = set()
    for v in (product or {}).get("variants", []):
        out.add(v.get("title") or "")
        out.update(v.get(f"option{i}") or "" for i in (1, 2, 3))
    return {n for n in (" ".join(re.findall(r"[a-z0-9]+", x.lower())) for x in out) if len(n) >= 4}


def extract_highlights(body_html: str | None, title: str = "", page: list[str] | None = None,
                       limit: int = 4, other_titles: list[str] | None = None,
                       product: dict | None = None) -> list[str]:
    pool = _Pool()
    lis, rest = body_lines(body_html)
    for li in lis:
        pool.add_line(li, li_base=2.0, prose_base=0.6)
    for line in rest:
        pool.add_line(line, li_base=1.2, prose_base=0.0)
    for line in page or []:
        pool.add_line(_split_spec_runs(line), li_base=1.45, prose_base=0.3)  # <1.5: must name a feature
    others = {" ".join(re.findall(r"[a-z0-9]+", t.lower())) for t in other_titles or [] if t != title}
    return pool.pick(title, limit, {t for t in others if len(t) >= 8}, _variant_strs(product))


def option_facts(p: dict) -> list[str]:
    """Factual fallback from the variant matrix, e.g. '9 colourways in stock'."""
    for i, opt in enumerate(p.get("options") or [], 1):
        name = (opt.get("name") or "").lower() if isinstance(opt, dict) else ""
        if re.search(r"colou?r|finish|style", name):
            vals = {v.get(f"option{i}") for v in p.get("variants", []) if v.get("available", True)} - {None}
            if len(vals) >= 3:
                return [f"{len(vals)} colourways in stock"]
    return []
