"""Product-title cleanup shared by ranking (dedupe keys) and rendering (slide headlines)."""

from __future__ import annotations

import re

_SKU = re.compile(r"\s+(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9]+(?:-[A-Z0-9]+)+(?=\s|$)")  # C2105-DS1, C20045-2
_LONG_PAREN = re.compile(r"\s*\([^)]{12,}\)")  # (3.98" Damascus Blade)
_COLOUR_IN = re.compile(r"\s+in\s+([A-Z][\w]*(?:\s+[A-Z][\w]*)?)\s*$")  # "... in Gobi Tan"


def clean(t: str) -> str:
    t = _LONG_PAREN.sub("", t.strip())
    t = _SKU.sub("", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def split_title(t: str, brand: str) -> tuple[str, str]:
    """-> (headline, sub-line). 'Disney | Orbitkey Sling (4L)' -> ('Sling (4L)', 'Disney')."""
    parts = [x.strip() for x in re.split(r"\s[-–|]\s", clean(t)) if x.strip()]
    swapped = (len(parts) > 1 and len(parts[0].split()) == 1 and len(parts[1].split()) >= 2
               and not parts[0].lower().startswith(brand.lower()))
    if swapped:  # collab/licence prefix in front of the real product name: "Disney | Orbitkey Sling"
        parts[0], parts[1] = parts[1], parts[0]
    name, sub = parts[0], " · ".join(parts[1:])
    stripped = re.sub(rf"^{re.escape(brand)}\s+", "", name, flags=re.I)
    if not swapped and len(stripped.split()) >= 2:  # keep "Ridge Wallet" rather than just "Wallet"
        name = stripped
    m = _COLOUR_IN.search(name)
    if m:
        name, sub = name[:m.start()], " · ".join(x for x in [sub, m.group(1)] if x)
    if len(name) > 32:  # keyword-stuffed titles: cut at a natural break after the model name
        m = re.search(r".{10,}?(?=\s(?:&|with|for|and|\+)\s|,)", name)
        if m:
            name = m.group(0).strip()
    return name, sub


def dedupe_key(t: str, brand: str) -> str:
    """Same first three words (brand removed) => treat as one product (colourways, handle/size variants)."""
    name, _ = split_title(t, brand)
    name = re.sub(rf"^{re.escape(brand)}\s+", "", name, flags=re.I)
    words = re.sub(r"[^a-z0-9 ]", "", name.lower()).split()
    return " ".join(words[:3])
