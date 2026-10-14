"""Canonical form of a source, used to detect the same item saved twice."""

import re
from urllib.parse import parse_qsl, urlencode, urlparse

TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|igsh|igshid|si|obrf|ref|ref_src|mc_cid|mc_eid|feature)$")


def canonical(url: str) -> str:
    p = urlparse(url.strip())
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [s for s in p.path.split("/") if s]

    if host == "youtu.be" and parts:
        return f"youtube:{parts[0]}"
    if host.endswith("youtube.com"):
        v = dict(parse_qsl(p.query)).get("v")
        if v:
            return f"youtube:{v}"
        if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
            return f"youtube:{parts[1]}"
    if host.endswith("instagram.com") and len(parts) >= 2 and parts[-2] in ("reel", "reels", "p", "tv"):
        return f"instagram:{parts[-1]}"

    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query) if not TRACKING.match(k)))
    path = p.path.rstrip("/")
    return f"{host}{path}" + (f"?{query}" if query else "")
