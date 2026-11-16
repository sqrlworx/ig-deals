"""Collect sale signals + discounted products from a brand's official site.

Two strategies, picked automatically per brand (or forced via `platform` in config):
  * shopify : public /products.json feed -> per-product price vs compare_at_price
  * promo   : homepage only -> sitewide promo banner text (e.g. "Up to 25% off sitewide")
Every brand also gets homepage promo extraction, so Shopify brands carry their banner text too.
"""

from __future__ import annotations

import html as htmllib
import re
import time
from dataclasses import asdict, dataclass, field

from .highlights import extract_highlights, option_facts, page_lines
from .net import fetch, fetch_json

BF_RE = re.compile(r"black\s*friday|bfcm|cyber\s*(monday|week|sale)|black\s*november|holiday\s*sale", re.I)
PCT_RE = re.compile(r"(\d{1,2})\s*%\s*off", re.I)
UPTO_RE = re.compile(r"up\s*to\s*(\d{1,2})\s*%", re.I)
CODE_RE = re.compile(r"\b(?i:code|coupon)\s*[:\-]?\s*[\"“']?([A-Z0-9]{4,20})\b")
NOISE_RE = re.compile(
    r"sign\s*up|subscribe|newsletter|e-?mail|register|is coming|coming soon|ends soon|"
    r"don.t miss|first order|welcome|sms|text\s+us|privacy|cookie|launches in|starts in|countdown",
    re.I,
)


@dataclass
class Promo:
    text: str
    score: float
    is_bf: bool
    max_pct: int | None
    code: str | None


@dataclass
class Deal:
    brand: str
    domain: str
    title: str
    handle: str
    url: str
    image: str | None
    product_type: str
    price: float
    compare_at: float
    pct_off: float
    saved: float
    currency: str
    variant: str | None
    tags: list[str] = field(default_factory=list)
    score: float = 0.0
    highlights: list[str] = field(default_factory=list)  # "why it's great" bullets


@dataclass
class BrandResult:
    name: str
    domain: str
    platform: str
    status: str
    currency: str = "USD"
    homepage_ok: bool = False
    bf_signal: bool = False
    promos: list[Promo] = field(default_factory=list)
    deals: list[Deal] = field(default_factory=list)
    catalog_size: int = 0
    discounted_count: int = 0
    notes: list[str] = field(default_factory=list)
    # scan-time only (not serialised): raw Shopify products by handle, for page enrichment
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def headline_promo(self) -> Promo | None:
        return self.promos[0] if self.promos else None

    def to_dict(self):
        d = asdict(self)
        d.pop("raw", None)
        d["headline_promo"] = asdict(self.headline_promo) if self.headline_promo else None
        return d


# ---------------------------------------------------------------- promo text

_BLOCK_TAGS = r"p|div|li|ul|ol|h[1-6]|section|header|footer|nav|button|tr|td|dialog|form|label|option|marquee"


def text_segments(page_html: str) -> list[str]:
    h = re.sub(r"(?is)<!--.*?-->", " ", page_html)
    h = re.sub(r"(?is)<(script|style|noscript|template|svg|head)\b.*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(rf"(?is)</?(?:{_BLOCK_TAGS})\b[^>]*>", "\n", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)  # inline tags -> space
    h = htmllib.unescape(h)
    segs = []
    for line in h.split("\n"):
        line = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]", " ", line)  # emoji
        s = re.sub(r"\s+", " ", line).strip(" |•·-–")
        if 6 <= len(s) <= 180:
            segs.append(s)
    return segs


def extract_promos(page_html: str, limit: int = 6) -> list[Promo]:
    seen, out = set(), []
    for s in text_segments(page_html):
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        is_bf = bool(BF_RE.search(s))
        pcts = [int(x) for x in PCT_RE.findall(s) + UPTO_RE.findall(s)]
        score = 0.0
        score += 3 if is_bf else 0
        score += 2 if pcts else 0
        score += 1 if re.search(r"site\s*-?\s*wide|everything|storewide|all\s+(bags|products|orders)", s, re.I) else 0
        score += 1 if re.search(r"\b(now|live|active|on now|starts? now|shop)\b", s, re.I) else 0
        score += 0.5 if UPTO_RE.search(s) else 0
        code = (CODE_RE.search(s) or [None, None])[1]
        score += 1 if code else 0
        score -= 2.5 if NOISE_RE.search(s) else 0
        score -= 1 if len(s) > 120 else 0
        if score >= 2 and (is_bf or pcts):
            out.append(Promo(s, score, is_bf, max(pcts) if pcts else None, code))
    out.sort(key=lambda p: (-p.score, p.max_pct is None, len(p.text)))
    return out[:limit]


# ---------------------------------------------------------------- shopify


def _img(src: str | None, width: int = 1000) -> str | None:
    if not src:
        return None
    if src.startswith("//"):
        src = "https:" + src
    return src + ("&" if "?" in src else "?") + f"width={width}"


def shopify_products(domain: str, max_pages: int) -> list[dict] | None:
    products: list[dict] = []
    for page in range(1, max_pages + 1):
        data = fetch_json(f"https://{domain}/products.json?limit=250&page={page}")
        if data is None or "products" not in data:
            return products or None
        batch = data["products"]
        products.extend(batch)
        if len(batch) < 250:
            break
        time.sleep(1.0)  # be polite between catalog pages
    return products


def product_to_deal(brand: str, domain: str, currency: str, p: dict) -> Deal | None:
    best = None
    for v in p.get("variants", []):
        if not v.get("available", True):
            continue
        try:
            price = float(v["price"])
            cmp_ = float(v.get("compare_at_price") or 0)
        except (TypeError, ValueError):
            continue
        if cmp_ <= price or price <= 0:
            continue
        pct = 1 - price / cmp_
        if best is None or (pct, cmp_ - price) > (best[0], best[1]):
            best = (pct, cmp_ - price, price, cmp_, v)
    if not best:
        return None
    pct, saved, price, cmp_, v = best
    img = (v.get("featured_image") or {}).get("src") if isinstance(v.get("featured_image"), dict) else None
    img = img or (p["images"][0]["src"] if p.get("images") else None)
    tags = p.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    vt = v.get("title")
    hl = extract_highlights(p.get("body_html"), p["title"], product=p)
    return Deal(
        brand=brand, domain=domain, title=p["title"].strip(), handle=p["handle"],
        url=f"https://{domain}/products/{p['handle']}", image=_img(img),
        product_type=p.get("product_type") or "", price=price, compare_at=cmp_,
        pct_off=round(pct, 4), saved=round(saved, 2), currency=currency,
        variant=None if vt in (None, "Default Title") else vt, tags=tags,
        highlights=(hl + option_facts(p))[:4],
    )


# ---------------------------------------------------------------- per brand


def collect_brand(brand: dict, max_pages: int = 4) -> BrandResult:
    name, domain = brand["name"], brand["domain"]
    platform = brand.get("platform", "auto")
    res = BrandResult(name=name, domain=domain, platform=platform, status="ok")

    status, home = fetch(f"https://{domain}/")
    res.homepage_ok = status == 200 and bool(home)
    if res.homepage_ok:
        res.promos = extract_promos(home)
    else:
        res.notes.append(f"homepage fetch failed (status {status})")
    for url in brand.get("extra_promo_urls", []):
        s, body = fetch(url)
        if s == 200:
            res.promos = sorted(res.promos + extract_promos(body), key=lambda p: -p.score)[:6]

    if brand.get("promo_override"):
        o = brand["promo_override"]
        res.promos.insert(0, Promo(o, 99, bool(BF_RE.search(o)),
                                   max([int(x) for x in PCT_RE.findall(o) + UPTO_RE.findall(o)] or [0]) or None,
                                   (CODE_RE.search(o) or [None, None])[1]))
        res.notes.append("promo_override from config")
    res.bf_signal = any(p.is_bf for p in res.promos)

    if platform in ("auto", "shopify"):
        meta = fetch_json(f"https://{domain}/meta.json") or {}
        res.currency = meta.get("currency") or "USD"
        prods = shopify_products(domain, max_pages)
        if prods is not None:
            res.platform = "shopify"
            res.catalog_size = len(prods)
            for p in prods:
                d = product_to_deal(name, domain, res.currency, p)
                if d:
                    res.deals.append(d)
                    res.raw[p["handle"]] = p
            res.discounted_count = len(res.deals)
        elif platform == "shopify":
            res.status = "error"
            res.notes.append("products.json unavailable")
        else:
            res.platform = "promo"
            res.notes.append("no products.json feed (non-Shopify, blocked or rate-limited); rerun later if unexpected")
    if res.platform == "promo":
        res.notes.append("promo-only (no public product feed); add `manual_deals` in config for product slides")

    for md in brand.get("manual_deals", []):
        price, cmp_ = float(md["price"]), float(md["compare_at"])
        res.deals.append(Deal(
            brand=name, domain=domain, title=md["title"], handle=md.get("handle", md["title"]),
            url=md["url"], image=md.get("image"), product_type=md.get("product_type", ""),
            price=price, compare_at=cmp_, pct_off=round(1 - price / cmp_, 4),
            saved=round(cmp_ - price, 2), currency=res.currency, variant=md.get("variant"),
            tags=["manual"], highlights=md.get("highlights", []),
        ))

    if not res.homepage_ok and not res.deals:
        res.status = "error"
    return res


# ---------------------------------------------------------------- bullets from product pages


def enrich_highlights(r: BrandResult, deals: list[Deal]) -> None:
    """Re-extract bullets for the deals that made a post, adding the product page as a source.

    Page text that also appears on the homepage (nav, footer, promo bar) is ignored, and so are
    names of the brand's other products (related-product tiles). One request per deal, cached per day.
    """
    if r.platform != "shopify" or not deals:
        return
    _, home_html = fetch(f"https://{r.domain}/")
    home = {s.lower() for s in text_segments(home_html or "")}
    titles = [p.get("title", "") for p in r.raw.values()]
    for d in deals:
        p = r.raw.get(d.handle)
        if p is None:
            continue
        status, body = fetch(d.url)
        pl = page_lines(text_segments(body), home, d.title) if status == 200 and body else []
        hl = extract_highlights(p.get("body_html"), d.title, pl, other_titles=titles, product=p)
        d.highlights = (hl + option_facts(p))[:4] or d.highlights
