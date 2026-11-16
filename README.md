# IG Black Friday deal carousels

Scans official brand websites for live sales and renders **one Instagram carousel post per brand**:
a brand title card (hero product plus two tiles), one slide per discounted item with 2–3 "why it's great"
bullets, and a closing slide.
Everything lands in one review HTML, which also has each post's caption, its product links,
a brand scan report and every qualifying deal. Python stdlib only, no installs.

```bash
cd ~/gdrive/claude/ig_deals
python3 run.py edc                 # scan → rank → render   (≈1–3 min, polite to Shopify rate limits)
python3 run.py edc --rerender      # re-render from today's scan.json after editing copy/pins/filters
python3 run.py edc --brands "Ridge,ALPAKA"   # scan a subset
python3 run.py edc --no-cache      # ignore today's HTTP cache
```

Output: `runs/<slug>/<YYYY-MM-DD>/posts.html`, plus `captions/NN_<brand>.txt` (caption and links per post),
`scan.json` and `ranked.json`. Open the HTML in a browser. Each post has **Download PNGs**, which exports
its slides at 1080×1350 (IG 4:5). **Download all PNGs** does every post.
Product images load from the brand's CDN, so you need to be online. `--embed-images` makes a fully
offline file, but it's slow because CDNs rate-limit bulk image downloads.

## How deals are found

| Step | What happens |
|---|---|
| Homepage promo scan | For every brand, fetch the homepage, strip scripts and templates, and score each text segment for "Black Friday / BFCM / Cyber", "% off", "sitewide", codes. Newsletter and "coming soon" noise is down-weighted. The best segment becomes the brand's headline promo, and `bf_signal` is set if any segment mentions Black Friday. |
| Product prices (Shopify) | Most DTC brands run on Shopify, which publishes `/products.json`. For every **in-stock** variant, compare `price` with `compare_at_price` and keep the deepest discount per product. |
| Promo-only brands | Some sites render prices client-side (for example Bellroy), so there are no item prices. These are skipped unless you add `manual_deals` to the config. Brands whose sale is only a checkout code (for example Craighill's GIFT2026) are skipped too, because there are no item markdowns to show. |
| Filtering | Apply `min/max_pct_off`, `min/max_price`, and exclude regexes (gift cards, apparel and so on). Products sharing their first three title words (colourways, handle or size variants) are collapsed. Titles are cleaned of SKUs and spec parentheticals. |
| Scoring | `0.55·pct_off + 0.35·log(saved)/log(cap)`, plus a bonus if the brand shows Black Friday messaging, plus a bonus for hero items (`boost_title_regex`), minus a penalty for `penalize_title_regex` (bundles/kits whose "was" price is a sum of parts, off-category items). |
| Slide bullets | For every item, short feature bullets are pulled from the brand's own copy: authored `<li>` bullets and spec rows ("Blade Material: Nitro-V" becomes "Nitro-V blade steel") in the Shopify description, then sentences or clauses that name a concrete feature. For items that make a post, the product page is read too (one request per item, cached), ignoring any text that also appears on the homepage (menus, footer, promo bar), related-product names, variant pickers and reviews. Many themes keep their feature callouts only on the page (e.g. Ridge's "Holds 1-12 Cards"). Bullets are factual brand copy, never invented. Fallback: "N colourways in stock". |
| Posts | `posts`: one post per brand with at least `min_deals` qualifying items, up to `max_deals` slides, at most `max_per_type` items per Shopify product type (so a post isn't eight wallets). Brands with Black Friday messaging come first, then the rest by deal strength. `require_bf_signal: true` drops brands without Black Friday messaging. The Brand Scan table says why each brand was skipped. |

## New category = new JSON file

Copy `categories/edc.json` to `categories/<slug>.json` and edit:

- `brands`: `{"name", "domain"}`. Optional fields:
  - `platform`: `"shopify"` or `"promo"`. Defaults to auto-detect.
  - `promo_override`: text to use when the banner scrape picks the wrong line.
  - `extra_promo_urls`: also scan a dedicated sale page.
  - `manual_deals`: `[{title, url, image, price, compare_at}]`.
- `filters`: thresholds plus `exclude_title_regex`, `include_title_regex` (keep only on-topic items for broad stores) and `boost_title_regex`.
- `pins`: `["domain/handle", ...]` forces products into that brand's post. Copy keys from the deals table.
- `filters.exclude_handles`: drop specific products.
- `bullets`: `{"max": 3, "warn_below": 2, "fetch_product_pages": true}`. Set `max` to 0 to drop bullets.
- `highlights`: `{"domain/handle": ["bullet", "bullet", "bullet"]}` replaces the auto bullets for a product.
  Each post in the review HTML has a **Slide bullets** box with the current bullets as JSON: edit it, paste it here,
  and run `--rerender`. Posts with slides under `warn_below` bullets get a warning above the rail.
- `theme`: colours and Google Fonts (inlined automatically).
- `content`: every piece of copy on the slides and in the caption, plus hashtags and handle. Placeholders:
  `{brand} {domain} {n} {max_pct} {category} {sale_name} {date} {promo} {brand_tag}`.
  Add a `_no_bf` suffix (`post_title_line_no_bf`, `caption_hook_no_bf`) for brands without Black Friday messaging.

Then run `python3 run.py <slug>`.

## Caveats

- `compare_at_price` is what the brand shows as the "was" price. Some brands keep evergreen markdowns, so check the **BF?** column and the candidates table before posting.
- Prices are captured at scan time. Rerun close to posting, since the cache is per-day.
- Bullets are heuristic too. Skim them in the review page. A few items get only 0–1 bullets when a brand's
  copy is pure prose (the warning lists them), and an occasional odd line slips through. Fix either with `highlights`.
- Promo text is heuristic. Always check the headline promo in the Brand Scan table, and use `promo_override` if it's off.
- Scans are throttled globally: one request every 0.8s or more across all stores. Shopify rate-limits per IP across every store, so any 429 pauses all workers for its Retry-After.
- Requests go through `curl`, with an honest scanner User-Agent, when it's installed. Shopify's edge returned 429 to Python's urllib from this machine even after throttling, while curl was served normally. urllib is the fallback.
- The HTTP cache is per day (`.cache/<date>/`), and only the last 2 days are kept. `--no-cache` forces fresh prices.
