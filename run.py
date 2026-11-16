#!/usr/bin/env python3
"""Black Friday deal carousel generator.

  python3 run.py edc                      # scan brands -> rank -> render HTML
  python3 run.py edc --rerender           # re-render latest run from saved scan (after editing config pins/copy)
  python3 run.py edc --no-cache           # force fresh fetches
  python3 run.py categories/my.json       # any category config path

Outputs to runs/<category>/<YYYY-MM-DD>/ : posts.html (one carousel per brand), captions/, scan.json, ranked.json
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from deals import net  # noqa: E402
from deals.render import render_page  # noqa: E402
from deals.select import build_posts, rank_all  # noqa: E402
from deals.sources import BrandResult, Deal, Promo, collect_brand, enrich_highlights  # noqa: E402


def load_cfg(arg: str) -> tuple[dict, str]:
    p = Path(arg)
    if not p.exists():
        p = ROOT / "categories" / f"{arg}.json"
    cfg = json.loads(p.read_text())
    return cfg, cfg.get("slug", p.stem)


def result_from_dict(d: dict) -> BrandResult:
    d = dict(d)
    d.pop("headline_promo", None)
    d["promos"] = [Promo(**p) for p in d["promos"]]
    d["deals"] = [Deal(**x) for x in d["deals"]]
    return BrandResult(**d)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("category", help="category slug in categories/ or path to a config json")
    ap.add_argument("--rerender", action="store_true", help="skip fetching; reuse today's scan.json")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-embed", action="store_true", help="don't inline Google Fonts")
    ap.add_argument("--embed-images", action="store_true",
                    help="inline product images (fully offline HTML; slow, CDNs rate-limit)")
    ap.add_argument("--brands", help="comma-separated subset of brand names to scan")
    ap.add_argument("--out", help="override output dir")
    args = ap.parse_args()

    cfg, slug = load_cfg(args.category)
    out = Path(args.out) if args.out else ROOT / "runs" / slug / date.today().isoformat()
    out.mkdir(parents=True, exist_ok=True)
    scan_path = out / "scan.json"

    brands = cfg["brands"]
    if args.brands:
        want = {b.strip().lower() for b in args.brands.split(",")}
        brands = [b for b in brands if b["name"].lower() in want]

    if args.rerender and scan_path.exists():
        results = [result_from_dict(d) for d in json.loads(scan_path.read_text())]
        print(f"loaded {len(results)} brands from {scan_path}")
    else:
        net.CACHE_ENABLED = not args.no_cache
        net.prune_cache()
        max_pages = cfg.get("max_catalog_pages", 4)
        print(f"scanning {len(brands)} brands…")
        with ThreadPoolExecutor(max_workers=cfg.get('concurrency', 4)) as ex:
            def one(b):
                r = collect_brand(b, max_pages)
                print(f"  ✓ {r.name} ({r.platform}, {r.discounted_count} on sale)", flush=True)
                return r
            results = list(ex.map(one, brands))
        scan_path.write_text(json.dumps([r.to_dict() for r in results], indent=1))

    for r in sorted(results, key=lambda r: r.name.lower()):
        hp = r.headline_promo.text[:70] if r.headline_promo else ""
        print(f"  {r.name:<20} {r.platform:<8} {r.status:<5} BF={'Y' if r.bf_signal else '-'} "
              f"sale={r.discounted_count:>3}/{r.catalog_size:<3} {hp}")

    ranked = rank_all(results, cfg)
    posts, skipped = build_posts(results, ranked, cfg)
    if not args.rerender and cfg.get("bullets", {}).get("fetch_product_pages", True):
        n = sum(len(p["deals"]) for p in posts)
        print(f"reading {n} product pages for slide bullets…")
        with ThreadPoolExecutor(max_workers=cfg.get("concurrency", 4)) as ex:
            list(ex.map(lambda p: enrich_highlights(p["brand"], p["deals"]), posts))
        scan_path.write_text(json.dumps([r.to_dict() for r in results], indent=1))
    (out / "ranked.json").write_text(json.dumps([asdict(d) for d in ranked], indent=1))

    page, built = render_page(cfg, results, ranked, posts, skipped, embed_fonts=not args.no_embed,
                              embed_images=args.embed_images,
                              run_slug=f"{slug}_{date.today():%Y%m%d}")
    (out / "posts.html").write_text(page)
    cap_dir = out / "captions"
    cap_dir.mkdir(exist_ok=True)
    for old in cap_dir.glob("*.txt"):
        old.unlink()
    for i, b in enumerate(built, 1):
        (cap_dir / f"{i:02d}_{b['slug']}.txt").write_text(b["caption"] + "\n\nLINKS\n" + b["links"] + "\n")
    for stale in ("carousel.html", "caption.txt"):  # outputs of the old single-roundup format
        (out / stale).unlink(missing_ok=True)

    print(f"\n{len(built)} posts:")
    for b in built:
        ds = b["deals"]
        print(f"  {b['brand'].name:<18} {'BF' if b['brand'].bf_signal else '  '} {len(ds)} deals, "
              f"{min(d.pct_off for d in ds):.0%}-{max(d.pct_off for d in ds):.0%} off: "
              + "; ".join(d.title[:28] for d in ds[:3]) + ("…" if len(ds) > 3 else ""))
    print(f"skipped {len(skipped)}:")
    for r, why in skipped:
        print(f"  {r.name:<18} {why}")
    print(f"\n→ {out / 'posts.html'}")


if __name__ == "__main__":
    main()
