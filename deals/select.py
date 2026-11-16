"""Filter, score and pick the deals that make it onto the carousel."""

from __future__ import annotations

import math
import re

from .sources import BrandResult, Deal
from .titles import dedupe_key

DEFAULT_FILTERS = {
    "min_pct_off": 0.15,
    "max_pct_off": 0.80,
    "min_price": 20,
    "max_price": 1000,
    "exclude_title_regex": r"gift\s*card|e-?gift|sticker|patch|decal|replacement|spare|warranty|"
                           r"shipping protection|route|insurance|donation|sample|test product|deposit",
    "exclude_product_types": [],
    "include_title_regex": None,
    "boost_title_regex": None,
    "penalize_title_regex": None,
    "exclude_handles": [],
}
DEFAULT_WEIGHTS = {"pct": 0.55, "saved": 0.35, "saved_cap": 120, "bf_bonus": 0.08, "boost": 0.08, "penalty": 0.12}
DEFAULT_POSTS = {"min_deals": 3, "max_deals": 8, "max_per_type": 3, "max_posts": 20,
                 "require_bf_signal": False}


def filter_deals(deals: list[Deal], f: dict) -> list[Deal]:
    ex = re.compile(f["exclude_title_regex"], re.I) if f.get("exclude_title_regex") else None
    inc = re.compile(f["include_title_regex"], re.I) if f.get("include_title_regex") else None
    ex_types = {t.lower() for t in f.get("exclude_product_types", [])}
    ex_handles = set(f.get("exclude_handles", []))
    out = []
    for d in deals:
        hay = f"{d.title} {d.product_type}"
        if not (f["min_pct_off"] <= d.pct_off <= f["max_pct_off"]):
            continue
        if not (f["min_price"] <= d.price <= f["max_price"]):
            continue
        if ex and ex.search(hay):
            continue
        if inc and not inc.search(hay):
            continue
        if d.product_type.lower() in ex_types or d.handle in ex_handles:
            continue
        if not d.image:
            continue
        out.append(d)
    return out


def score_deal(d: Deal, bf_brand: bool, w: dict, boost_re, penalty_re=None) -> float:
    s = w["pct"] * d.pct_off
    s += w["saved"] * min(math.log1p(d.saved) / math.log1p(w["saved_cap"]), 1.0)
    s += w["bf_bonus"] if bf_brand else 0
    if boost_re and boost_re.search(f"{d.title} {d.product_type}"):
        s += w["boost"]
    if penalty_re and penalty_re.search(d.title):  # e.g. bundles whose "was" price is a sum of parts
        s -= w["penalty"]
    return round(s, 4)


def rank_all(results: list[BrandResult], cfg: dict) -> list[Deal]:
    f = {**DEFAULT_FILTERS, **cfg.get("filters", {})}
    w = {**DEFAULT_WEIGHTS, **cfg.get("weights", {})}
    boost_re = re.compile(f["boost_title_regex"], re.I) if f.get("boost_title_regex") else None
    penalty_re = re.compile(f["penalize_title_regex"], re.I) if f.get("penalize_title_regex") else None
    ranked = []
    for r in results:
        seen = set()
        for d in sorted(filter_deals(r.deals, f), key=lambda d: -d.pct_off):
            key = dedupe_key(d.title, r.name)
            if key in seen:  # collapse colourways listed as separate products
                continue
            seen.add(key)
            d.score = score_deal(d, r.bf_signal, w, boost_re, penalty_re)
            ranked.append(d)
    ranked.sort(key=lambda d: -d.score)
    return ranked


def build_posts(results: list[BrandResult], ranked: list[Deal], cfg: dict) -> tuple[list[dict], list[tuple]]:
    """One carousel post per brand. Returns (posts, skipped).

    post = {"brand": BrandResult, "deals": [Deal, ...]}; skipped = [(BrandResult, reason), ...]
    Within a brand: highest score first, at most `max_per_type` items per product_type so a post
    isn't eight colourways of one wallet; pins (`domain/handle`) are always included first.
    """
    p = {**DEFAULT_POSTS, **cfg.get("posts", {})}
    pins = set(cfg.get("pins", []))
    by_brand: dict[str, list[Deal]] = {}
    for d in ranked:  # ranked is already score-sorted
        by_brand.setdefault(d.brand, []).append(d)

    posts, skipped = [], []
    for r in results:
        if r.status != "ok":
            skipped.append((r, "scan failed: " + "; ".join(r.notes)))
            continue
        if p["require_bf_signal"] and not r.bf_signal:
            skipped.append((r, "no Black Friday messaging on homepage"))
            continue
        cands = by_brand.get(r.name, [])
        pinned = [d for d in cands if f"{d.domain}/{d.handle}" in pins]
        chosen: list[Deal] = list(pinned[: p["max_deals"]])
        per_type: dict[str, int] = {}
        for d in chosen:
            per_type[d.product_type.lower()] = per_type.get(d.product_type.lower(), 0) + 1
        for d in cands:
            if len(chosen) >= p["max_deals"]:
                break
            t = d.product_type.lower()
            if d in chosen or (t and per_type.get(t, 0) >= p["max_per_type"]):
                continue
            chosen.append(d)
            per_type[t] = per_type.get(t, 0) + 1
        if len(chosen) < p["min_deals"]:  # relax type cap only to reach the minimum
            for d in cands:
                if len(chosen) >= p["min_deals"]:
                    break
                if d not in chosen:
                    chosen.append(d)
        if len(chosen) < p["min_deals"]:
            hp = r.headline_promo
            if r.platform != "shopify":
                why = "no product prices (promo-only site); add manual_deals"
            elif not chosen and hp and (hp.code or hp.max_pct):
                why = f"no item markdowns; sale is a code/sitewide promo (“{hp.text[:70]}”)"
            else:
                why = f"only {len(chosen)} qualifying deal(s)"
            skipped.append((r, why))
            continue
        chosen.sort(key=lambda d: -d.score)
        strength = sum(d.score for d in chosen[:3]) / 3
        posts.append({"brand": r, "deals": chosen, "strength": strength})

    posts.sort(key=lambda x: (not x["brand"].bf_signal, -x["strength"]))
    for extra in posts[p["max_posts"]:]:
        skipped.append((extra["brand"], f"over max_posts ({p['max_posts']})"))
    return posts[: p["max_posts"]], skipped
