"""Render one IG carousel per brand (1080x1350 slides) + captions into a single review HTML."""

from __future__ import annotations

import base64
import html
import json
import re
from datetime import date

from .net import fetch
from .sources import BrandResult, Deal
from .titles import split_title

CUR = {"USD": "$", "CAD": "CA$", "AUD": "A$", "NZD": "NZ$", "EUR": "€", "GBP": "£", "JPY": "¥"}
DEFAULT_THEME = {
    "bg": "#0e0e0e", "fg": "#f3efe6", "accent": "#ff5b1f", "card": "#ece8df",
    "muted": "#8d897f", "ink": "#151515",
    "display_font": "Anton", "body_font": "Inter",
}
esc = html.escape


# ------------------------------------------------------------ helpers

def money(x: float, cur: str) -> str:
    sym = CUR.get(cur, cur + " ")
    return f"{sym}{x:,.0f}" if abs(x - round(x)) < 0.005 else f"{sym}{x:,.2f}"


def sniff_mime(b: bytes) -> str:
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if b.lstrip()[:4] in (b"<svg", b"<?xm"):
        return "image/svg+xml"
    return "application/octet-stream"


def sized(url: str, width: int = 800) -> str:
    return re.sub(r"([?&])width=\d+", rf"\g<1>width={width}", url)


def embed_image(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("data:"):
        return url
    # Shopify's CDN serves WebP when asked, ~3-10x smaller than the JPEG/PNG originals
    url = sized(url)
    status, body = fetch(url, binary=True, accept="image/webp,image/*;q=0.8")
    if status != 200 or not body:
        return url  # hotlink fallback
    return f"data:{sniff_mime(body)};base64,{base64.b64encode(body).decode()}"


def embed_google_fonts(families: list[str]) -> str:
    """Download latin woff2 subsets and inline them so PNG export + offline review keep the fonts."""
    q = "&".join(f"family={f.replace(' ', '+')}:wght@400;500;600;700;800;900" if f != "Anton"
                 else "family=Anton" for f in families)
    status, css = fetch(f"https://fonts.googleapis.com/css2?{q}&display=swap")
    if status != 200 or not css:
        return ""
    out = []
    for block in re.findall(r"/\*\s*latin\s*\*/\s*(@font-face\s*{[^}]+})", css):
        m = re.search(r"url\((https://[^)]+\.woff2)\)", block)
        if not m:
            continue
        s, data = fetch(m.group(1), binary=True)
        if s == 200 and data:
            uri = "data:font/woff2;base64," + base64.b64encode(data).decode()
            out.append(block.replace(m.group(1), uri))
    return "\n".join(out)


# ------------------------------------------------------------ slides

def _footer(left: str, idx: int, total: int, swipe: bool = True) -> str:
    arrow = '<span class="swipe">SWIPE <b>→</b></span>' if swipe else ""
    return (f'<div class="foot"><span class="dom">{esc(left)}</span>{arrow}'
            f'<span class="idx">{idx:02d}/{total:02d}</span></div>')


def _lines(s: str) -> str:
    return "<br>".join(esc(x) for x in s.split("\n"))


def post_vars(r: BrandResult, deals: list[Deal], c: dict, checked: str) -> dict:
    hp = r.headline_promo
    return {
        "brand": r.name, "domain": r.domain, "n": len(deals), "date": checked,
        "max_pct": max(round(d.pct_off * 100) for d in deals),
        "category": c.get("category_label", "deals"),
        "sale_name": c.get("sale_name_bf", "Black Friday") if r.bf_signal else c.get("sale_name_no_bf", "sale"),
        "promo": hp.text if hp else "",
        "brand_tag": "#" + re.sub(r"[^a-z0-9]", "", r.name.lower()),
        "bf": r.bf_signal,
    }


def _pick(c: dict, key: str, bf: bool) -> str:
    """Use `<key>_no_bf` copy for brands without Black Friday messaging, when the config provides it."""
    return c[key] if bf else c.get(f"{key}_no_bf", c[key])


def brand_title_slide(r: BrandResult, deals: list[Deal], c: dict, v: dict, imgs: dict, total: int) -> str:
    tiles = "".join(
        f'<div class="tile"><img crossorigin="anonymous" src="{imgs.get(d.image) or ""}" alt=""><span class="tsticker">-{round(d.pct_off*100)}%</span></div>'
        for d in deals[:3]
    )
    n = len(r.name)
    size = 210 if n <= 6 else 180 if n <= 9 else 150 if n <= 12 else 120 if n <= 16 else 96
    kicker = c["kicker"] if r.bf_signal else c.get("kicker_no_bf", "ON SALE NOW")
    hp = r.headline_promo
    promo = ""
    if hp and (hp.max_pct or hp.code) and r.bf_signal:
        code = f' <b class="pcode">CODE {esc(hp.code)}</b>' if hp.code else ""
        promo = f'<div class="bpromo">“{esc(hp.text)}”{code}</div>'
    return f"""
<section class="slide s-title s-brand">
  <div class="kicker">{esc(kicker)}</div>
  <h1 style="font-size:{size}px">{esc(r.name)}</h1>
  <div class="bline">{_lines(_pick(c, "post_title_line", r.bf_signal).format(**v))}</div>
  <p class="sub">{esc(c["post_subtitle"].format(**v))}</p>
  {promo}
  <div class="grid">{tiles}</div>
  {_footer(c.get('handle', ''), 1, total)}
</section>"""


def deal_slide(d: Deal, r: BrandResult, imgs: dict, idx: int, total: int, c: dict,
               bullets: list[str] | None = None) -> str:
    name, sub = split_title(d.title, d.brand)
    if sub and d.variant and sub.lower() in d.variant.lower():
        sub = ""
    sub = " · ".join(x for x in [sub, d.variant] if x)
    pct = round(d.pct_off * 100)
    ribbon = f'<span class="ribbon">{esc(c.get("ribbon", "BLACK FRIDAY"))}</span>' if r.bf_signal else ""
    why = "".join(f"<li><span>{esc(b)}</span></li>" for b in bullets or [])
    why = f'<ul class="why">{why}</ul>' if why else ""
    return f"""
<section class="slide s-deal">
  <div class="top"><span class="brand">{esc(d.brand)}</span>{ribbon}</div>
  <div class="card">
    <img crossorigin="anonymous" src="{imgs.get(d.image) or ''}" alt="{esc(name)}">
    <div class="sticker"><b>{pct}%</b><i>OFF</i></div>
  </div>
  <h2>{esc(name)}</h2>
  <div class="var">{esc(sub)}</div>
  {why}
  <div class="prices">
    <span class="now">{money(d.price, d.currency)}</span>
    <span class="was">{money(d.compare_at, d.currency)}</span>
    <span class="save">SAVE {money(d.saved, d.currency)}</span>
  </div>
  {_footer(d.domain, idx, total)}
</section>"""


def cta_slide(c: dict, v: dict, idx: int, total: int) -> str:
    items = "".join(f"<li><span>{i+1}</span>{esc(t.format(**v))}</li>" for i, t in enumerate(c["cta_points"]))
    return f"""
<section class="slide s-cta">
  <div class="kicker">{esc(v['brand'].upper())} · {esc(v['sale_name'].upper())}</div>
  <h1>{_lines(c["cta_title"].format(**v))}</h1>
  <ul>{items}</ul>
  <div class="fine">{esc(c['disclaimer'].format(**v))}</div>
  {_footer(c.get('handle', ''), idx, total, swipe=False)}
</section>"""


def build_caption(c: dict, v: dict, deals: list[Deal]) -> str:
    lines = [_pick(c, "caption_hook", v["bf"]).format(**v), c["caption_intro"].format(**v), ""]
    for i, d in enumerate(deals, 1):
        name, _ = split_title(d.title, d.brand)
        lines.append(f"{i}. {name}: {money(d.price, d.currency)} "
                     f"(was {money(d.compare_at, d.currency)}, -{round(d.pct_off*100)}%)")
    lines += ["", c["caption_cta"].format(**v), "", " ".join([v["brand_tag"]] + c["hashtags"])]
    return "\n".join(lines)


def deal_bullets(d: Deal, bopts: dict) -> tuple[list[str], bool]:
    """(bullets, overridden). Config `highlights["domain/handle"]` wins over auto-extracted ones."""
    key = f"{d.domain}/{d.handle}"
    if key in bopts["overrides"]:
        return list(bopts["overrides"][key])[: bopts["max"]], True
    return d.highlights[: bopts["max"]], False


def build_post(post: dict, c: dict, imgs: dict, checked: str, bopts: dict) -> dict:
    r, deals = post["brand"], post["deals"]
    v = post_vars(r, deals, c, checked)
    total = len(deals) + 2
    bullets = [deal_bullets(d, bopts) for d in deals]
    slides = [brand_title_slide(r, deals, c, v, imgs, total)]
    slides += [deal_slide(d, r, imgs, i, total, c, bullets[i - 2][0]) for i, d in enumerate(deals, 2)]
    slides.append(cta_slide(c, v, total, total))
    links = "\n".join(f"{split_title(d.title, d.brand)[0]}: {d.url}" for d in deals)
    return {"brand": r, "deals": deals, "slides": slides, "caption": build_caption(c, v, deals), "bullets": bullets,
            "links": links, "slug": re.sub(r"[^a-z0-9]+", "-", r.name.lower()).strip("-")}


# ------------------------------------------------------------ page

CSS = r"""
:root{--s:.30}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:#2a2a2a;color:#eee;font:15px/1.45 var(--body),system-ui,sans-serif;padding:28px}
header.page h1{font:400 34px var(--display),Impact,sans-serif;letter-spacing:.02em}
header.page p{color:#aaa;margin:4px 0 14px}
nav.posts{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 18px}
nav.posts a{color:#eee;text-decoration:none;background:#1c1c1c;border:1px solid #333;border-radius:999px;padding:6px 12px;font-size:13px}
nav.posts a b{color:var(--accent)}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:10px 0 14px}
.toolbar button{background:var(--accent);color:#111;border:0;border-radius:8px;padding:8px 13px;font-weight:700;cursor:pointer}
.toolbar button.ghost{background:#333;color:#eee}
.toolbar label{color:#bbb;font-size:13px}
article.post{background:#1f1f1f;border-radius:14px;padding:20px 22px;margin-top:26px}
article.post h2.ph{font:400 30px var(--display),Impact,sans-serif;letter-spacing:.03em}
article.post h2.ph .bf{font:800 12px var(--body);letter-spacing:.14em;color:var(--ink);background:var(--accent);padding:4px 8px;border-radius:6px;vertical-align:6px;margin-left:8px}
article.post .meta{color:#aaa;font-size:13px}
.rail{display:flex;gap:14px;overflow-x:auto;padding-bottom:8px}
.frame{width:calc(1080px*var(--s));height:calc(1350px*var(--s));position:relative;flex:none;border-radius:10px;overflow:hidden;box-shadow:0 8px 30px #0008}
.slide{width:1080px;height:1350px;transform:scale(var(--s));transform-origin:0 0;position:absolute;inset:0;
  background:var(--bg);color:var(--fg);font-family:var(--body),system-ui,sans-serif;padding:76px 80px 0;overflow:hidden}
.slide:before{content:"";position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(1200px 600px at 110% -10%,color-mix(in srgb,var(--accent) 22%,transparent),transparent 60%)}
.slide>*{position:relative}
.kicker{display:inline-block;background:var(--accent);color:var(--ink);font:800 30px var(--body);letter-spacing:.14em;padding:12px 22px;border-radius:999px}
.foot{position:absolute;left:80px;right:80px;bottom:52px;display:flex;align-items:center;gap:24px;font:600 26px var(--body);color:var(--muted);letter-spacing:.06em}
.foot .dom{flex:1;text-transform:lowercase}
.foot .swipe{color:var(--fg)} .foot .swipe b{color:var(--accent)}
/* brand title */
.s-brand h1{font-family:var(--display),Impact,sans-serif;font-weight:400;line-height:.92;text-transform:uppercase;margin:40px 0 6px;letter-spacing:.01em}
.s-brand .bline{font:400 88px/.95 var(--display),Impact,sans-serif;text-transform:uppercase;color:var(--accent)}
.s-brand .sub{font:500 34px/1.3 var(--body);color:#cfcabe;max-width:900px;margin-top:28px}
.s-brand .bpromo{margin-top:22px;font:600 28px/1.35 var(--body);color:var(--fg);max-width:900px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.s-brand .pcode{background:var(--fg);color:var(--ink);padding:2px 10px;border-radius:8px;margin-left:8px;font-size:24px;letter-spacing:.08em}
.s-brand .grid{position:absolute;left:80px;right:80px;bottom:130px;height:520px;display:grid;grid-template-columns:1.55fr 1fr;grid-template-rows:1fr 1fr;gap:20px}
.s-brand .grid .tile{aspect-ratio:auto}
.s-brand .grid .tile:first-child{grid-row:span 2}
.s-brand .grid .tile:first-child .tsticker{font-size:34px;padding:8px 14px;top:16px;right:16px}
.tile{background:var(--card);border-radius:22px;aspect-ratio:1;position:relative;overflow:hidden;display:flex;align-items:center;justify-content:center}
.tile img{width:86%;height:86%;object-fit:contain;mix-blend-mode:multiply}
.tsticker{position:absolute;top:10px;right:10px;background:var(--accent);color:var(--ink);font:800 24px var(--body);padding:6px 10px;border-radius:10px}
/* deal */
.top{display:flex;align-items:center;justify-content:space-between}
.brand{font:800 40px var(--body);letter-spacing:.2em;text-transform:uppercase}
.ribbon{font:800 22px var(--body);letter-spacing:.16em;color:var(--accent);border:3px solid var(--accent);padding:8px 16px;border-radius:999px}
.s-deal{display:flex;flex-direction:column;padding-bottom:128px}
.card{margin-top:30px;flex:1 1 0;min-height:360px;background:var(--card);border-radius:36px;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden}
.card img{max-width:84%;max-height:88%;object-fit:contain;mix-blend-mode:multiply}
.sticker{position:absolute;top:26px;right:26px;width:180px;height:180px;border-radius:50%;background:var(--accent);color:var(--ink);
  display:flex;flex-direction:column;align-items:center;justify-content:center;transform:rotate(-8deg);box-shadow:0 10px 30px #0004}
.sticker b{font:400 72px/1 var(--display),Impact,sans-serif}
.sticker i{font:800 26px var(--body);font-style:normal;letter-spacing:.2em}
.s-deal h2{font:400 74px/1.0 var(--display),Impact,sans-serif;text-transform:uppercase;margin-top:34px;flex:none;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.var{font:500 28px var(--body);color:var(--muted);margin-top:8px;flex:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.why{list-style:none;margin-top:24px;display:flex;flex-direction:column;gap:14px;flex:none}
.why li{display:flex;align-items:flex-start;gap:22px;font:600 31px/1.25 var(--body);color:var(--fg)}
.why li:before{content:"";flex:none;width:12px;height:22px;margin:3px 6px 0 8px;border:solid var(--accent);border-width:0 6px 6px 0;transform:rotate(45deg)}
.why li span{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.prices{display:flex;align-items:baseline;gap:26px;margin-top:28px;flex:none}
.now{font:400 110px/1 var(--display),Impact,sans-serif;color:var(--accent)}
.was{font:600 46px var(--body);color:var(--muted);text-decoration:line-through;text-decoration-thickness:4px}
.save{margin-left:auto;font:800 28px var(--body);letter-spacing:.08em;background:var(--fg);color:var(--ink);padding:12px 18px;border-radius:12px}
/* cta */
.s-cta h1{font:400 128px/0.95 var(--display),Impact,sans-serif;text-transform:uppercase;margin:44px 0 60px}
.s-cta ul{list-style:none;display:flex;flex-direction:column;gap:30px}
.s-cta li{font:600 44px/1.2 var(--body);display:flex;gap:28px;align-items:center}
.s-cta li span{flex:none;width:78px;height:78px;border-radius:50%;background:var(--accent);color:var(--ink);display:flex;align-items:center;justify-content:center;font:400 44px var(--display),Impact}
.s-cta .fine{position:absolute;left:80px;right:80px;bottom:130px;font:500 24px/1.4 var(--body);color:var(--muted)}
/* review panels */
.cols{display:grid;grid-template-columns:2fr 1fr;gap:14px}
.cols3{display:grid;grid-template-columns:1.3fr 1.3fr 1fr;gap:14px}
.cols3 label{font-size:12px;color:#999;display:flex;flex-direction:column;gap:4px}
.warn{background:#3a2a12;color:#ffcf8a;border-radius:8px;padding:8px 12px;margin:10px 0;font-size:13px}
textarea{width:100%;height:260px;background:#111;color:#eee;border:1px solid #333;border-radius:8px;padding:12px;font:13px/1.5 ui-monospace,monospace}
section.panel{background:#1c1c1c;border-radius:12px;padding:20px 22px;margin-top:28px}
section.panel h2{font:700 18px var(--body);margin-bottom:12px;letter-spacing:.04em}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #2f2f2f;vertical-align:top}
th{color:#aaa;font-weight:600}
td a{color:#9cc4ff}
.ok{color:#7ddc8a}.bad{color:#ff8a80}.yes{color:var(--accent);font-weight:700}
tr.chosen td{background:#2b2418}
"""

JS = r"""
const s=document.getElementById('scale');
const setS=v=>{document.documentElement.style.setProperty('--s',v);s.value=v;};
s.oninput=()=>setS(s.value);
const qs=new URLSearchParams(location.search).get('scale'); if(qs) setS(qs);
document.querySelectorAll('[data-copy]').forEach(b=>b.onclick=()=>{
  navigator.clipboard.writeText(document.getElementById(b.dataset.copy).value);b.textContent='Copied ✓';
  setTimeout(()=>b.textContent='Copy caption',1200);});
async function loadLib(){ if(window.htmlToImage) return;
  await new Promise((ok,err)=>{const t=document.createElement('script');
  t.src='https://cdn.jsdelivr.net/npm/html-to-image@1.11.11/dist/html-to-image.js';t.onload=ok;t.onerror=err;document.head.appendChild(t);});}
async function exportPost(art,btn){
  const label=btn.textContent; btn.textContent='Rendering…'; await loadLib();
  const slides=[...art.querySelectorAll('.slide')];
  for(let i=0;i<slides.length;i++){
    const url=await htmlToImage.toPng(slides[i],{width:1080,height:1350,pixelRatio:1,style:{transform:'none'}});
    const a=document.createElement('a');a.href=url;a.download=`${document.body.dataset.slug}_${art.id}_${String(i+1).padStart(2,'0')}.png`;a.click();
    await new Promise(r=>setTimeout(r,350));
  }
  btn.textContent=label;
}
document.querySelectorAll('[data-export]').forEach(b=>b.onclick=()=>exportPost(document.getElementById(b.dataset.export),b));
document.getElementById('dlall').onclick=async e=>{for(const a of document.querySelectorAll('article.post')) await exportPost(a,e.target);};
"""


def render_page(cfg: dict, results: list[BrandResult], ranked: list[Deal], posts: list[dict],
                skipped: list[tuple], embed_fonts: bool = True, embed_images: bool = False,
                run_slug: str = "carousel") -> tuple[str, list[dict]]:
    theme = {**DEFAULT_THEME, **cfg.get("theme", {})}
    c = cfg["content"]
    checked = date.today().strftime("%b %-d, %Y")

    all_deals = [d for p in posts for d in p["deals"]]
    # Images are hotlinked from the brand CDN by default (CORS-enabled, so PNG export still works);
    # --embed-images inlines them for a fully offline file but is slow (CDNs rate-limit bulk fetches).
    imgs = {d.image: (embed_image(d.image) if embed_images else sized(d.image)) for d in all_deals if d.image}
    fonts_css = embed_google_fonts([theme["display_font"], theme["body_font"]]) if embed_fonts else ""

    bcfg = cfg.get("bullets", {})
    bopts = {"max": int(bcfg.get("max", 3)), "warn_below": int(bcfg.get("warn_below", 2)),
             "overrides": cfg.get("highlights", {})}
    built = [build_post(p, c, imgs, checked, bopts) for p in posts]

    nav = "".join(f'<a href="#{b["slug"]}">{esc(b["brand"].name)} <b>{len(b["deals"])}</b></a>' for b in built)
    articles = []
    for b in built:
        r = b["brand"]
        hp = r.headline_promo.text if r.headline_promo else "—"
        rail = "".join(f'<div class="frame">{s}</div>' for s in b["slides"])
        # editable overrides snippet: paste into the category config's "highlights" to fix bullets
        snippet = json.dumps({f"{d.domain}/{d.handle}": bl for d, (bl, _) in zip(b["deals"], b["bullets"])},
                             indent=2, ensure_ascii=False)
        thin = [split_title(d.title, d.brand)[0] for d, (bl, ov) in zip(b["deals"], b["bullets"])
                if not ov and len(bl) < bopts["warn_below"]]
        warn = (f'<div class="warn">⚠ fewer than {bopts["warn_below"]} bullets found for: {esc(", ".join(thin))}. '
                f'Edit the bullets JSON below and paste it into <code>"highlights"</code> in the config.</div>'
                if thin and bopts["max"] else "")
        articles.append(f"""
<article class="post" id="{b['slug']}">
  <h2 class="ph">{esc(r.name)}{'<span class="bf">BLACK FRIDAY</span>' if r.bf_signal else ''}</h2>
  <div class="meta">{len(b['slides'])} slides · {len(b['deals'])} deals · {r.discounted_count}/{r.catalog_size} products discounted on
    <a href="https://{r.domain}" target=_blank style="color:#9cc4ff">{esc(r.domain)}</a> · homepage promo: {esc(hp)}</div>
  <div class="toolbar"><button data-export="{b['slug']}">Download PNGs</button>
    <button class="ghost" data-copy="cap-{b['slug']}">Copy caption</button></div>
  {warn}
  <div class="rail">{rail}</div>
  <div class="cols3" style="margin-top:12px">
    <label>Caption<textarea id="cap-{b['slug']}">{esc(b['caption'])}</textarea></label>
    <label>Slide bullets: edit and paste into "highlights" in the config<textarea>{esc(snippet)}</textarea></label>
    <label>Links<textarea>{esc(b['links'])}</textarea></label>
  </div>
</article>""")

    status = {p["brand"].name: f"post · {len(p['deals'])} deals" for p in posts}
    status.update({r.name: f"skipped: {why}" for r, why in skipped})
    brand_rows = "".join(
        f"<tr><td><a href='https://{r.domain}' target=_blank>{esc(r.name)}</a></td><td>{r.platform}</td>"
        f"<td class='{'yes' if r.bf_signal else ''}'>{'yes' if r.bf_signal else '—'}</td>"
        f"<td>{esc(r.headline_promo.text) if r.headline_promo else '—'}</td>"
        f"<td>{r.discounted_count}/{r.catalog_size}</td>"
        f"<td class='{'ok' if status.get(r.name, '').startswith('post') else 'bad'}'>{esc(status.get(r.name, ''))}</td>"
        f"<td>{esc('; '.join(r.notes))}</td></tr>"
        for r in sorted(results, key=lambda r: (not status.get(r.name, '').startswith('post'), r.name.lower()))
    )
    chosen_ids = {id(d) for d in all_deals}
    deal_rows = "".join(
        f"<tr class='{'chosen' if id(d) in chosen_ids else ''}'><td>{esc(d.brand)}</td>"
        f"<td><a href='{esc(d.url)}' target=_blank>{esc(d.title)}</a>{' — ' + esc(d.variant) if d.variant else ''}</td>"
        f"<td>{esc(d.product_type)}</td><td>{money(d.price, d.currency)}</td><td>{money(d.compare_at, d.currency)}</td>"
        f"<td>{round(d.pct_off*100)}%</td><td>{d.score:.3f}</td><td><code>{esc(d.domain)}/{esc(d.handle)}</code></td></tr>"
        for d in sorted(ranked, key=lambda d: (d.brand.lower(), -d.score))
    )

    root_vars = (f":root{{--bg:{theme['bg']};--fg:{theme['fg']};--accent:{theme['accent']};--card:{theme['card']};"
                 f"--muted:{theme['muted']};--ink:{theme['ink']};--display:'{theme['display_font']}';--body:'{theme['body_font']}'}}")
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{esc(cfg['name'])} – IG posts review</title>
<style>{fonts_css}
{root_vars}
{CSS}</style></head>
<body data-slug="{esc(run_slug)}">
<header class="page"><h1>{esc(cfg['name'])} · {len(built)} brand posts</h1>
<p>{len(results)} brands scanned · {sum(r.bf_signal for r in results)} with Black Friday messaging ·
{len(skipped)} skipped (see Brand Scan) · prices checked {checked}</p></header>
<nav class="posts">{nav}</nav>
<div class="toolbar"><button id="dlall">Download all PNGs</button>
<label>Preview scale <input id="scale" type="range" min=".2" max="1" step=".02" value=".30"></label></div>
{''.join(articles)}
<section class="panel"><h2>BRAND SCAN</h2><table><tr><th>Brand</th><th>Source</th><th>BF?</th>
<th>Headline promo (homepage)</th><th>Discounted / catalog</th><th>Result</th><th>Notes</th></tr>{brand_rows}</table></section>
<section class="panel"><h2>ALL QUALIFYING DEALS (highlighted = in a post; pin/exclude via <code>domain/handle</code> in config)</h2>
<table><tr><th>Brand</th><th>Product</th><th>Type</th><th>Now</th><th>Was</th><th>Off</th><th>Score</th><th>Key</th></tr>{deal_rows}</table></section>
<script>{JS}</script></body></html>"""
    return page, built
