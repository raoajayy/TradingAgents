#!/usr/bin/env python3
"""Mirror the TradingView Charting Library from thefundedroom.com into frontend/public/.

The TV widget iframe must be same-origin, so the library (a small loader plus
~100 hashed webpack chunks under bundles/) has to be served from our origin.
This script discovers every chunk by scraping direct "bundles/..." references
and by parsing the webpack runtime chunk manifests ({id: "hash"} maps feeding
`+"."+map[id]+".js"` expressions), then downloads the closure.

The upstream SPA answers 404s with index.html, so any HTML payload on a
directly-referenced path is a hard error; manifest-derived guesses that come
back as HTML are skipped with a warning (some chunk ids are build-internal).

Usage: python scripts/mirror_charting_library.py [--base https://thefundedroom.com]
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://thefundedroom.com"
LIB_ROOT = "charting_library"
LANGS = ["en"]
DEST = Path(__file__).resolve().parent.parent / "frontend" / "public" / LIB_ROOT

DIRECT_REF_RE = re.compile(r'"(bundles/[^"?*]+?\.(?:js|css))"')
# webpack 5 runtime filename builders (ternary chains + id→name/id→hash
# maps); evaluated verbatim with node — regex reconstruction guessed wrong
# names for named chunks like get-error-card.<hash>.js
CHUNK_FN_RES = (
    re.compile(r'\.u=(e=>[\s\S]*?\+"\.js")'),
    re.compile(r'\.miniCssF=(e=>[\s\S]*?\+"\.css")'),
)
CHUNK_ID_RE = re.compile(r"(?:^|[{,(:?>])\s*(\d+)\s*(?:===|:)")
CSS_URL_RE = re.compile(r'url\(\s*["\']?([^"\')?#]+)["\']?\s*[)?#]')


def fetch(base: str, rel: str) -> bytes:
    req = urllib.request.Request(
        f"{base}/{LIB_ROOT}/{rel}", headers={"User-Agent": "tv-mirror/1.0"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def looks_like_html(payload: bytes) -> bool:
    head = payload[:200].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


def manifest_files(text: str) -> set[str]:
    """Evaluate the webpack runtime's chunk-filename functions with node —
    the only source of truth for hashed chunk names (a name map, a hash
    map, and special-case ternaries are all baked into the functions)."""
    import json
    import subprocess

    found: set[str] = set()
    for pattern in CHUNK_FN_RES:
        match = pattern.search(text)
        if not match:
            continue
        fn_src = match.group(1)
        ids = sorted({int(n) for n in CHUNK_ID_RE.findall(fn_src)})
        script = (
            f"const u={fn_src};const ids={json.dumps(ids)};"
            "const out=[];for(const i of ids){try{const f=u(i);"
            "if(typeof f==='string'&&!f.includes('undefined'))out.push(f);}"
            "catch(e){}}console.log(JSON.stringify(out));"
        )
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise RuntimeError(f"node manifest eval failed: {result.stderr[:200]}")
        found.update(f"bundles/{name}" for name in json.loads(result.stdout))
    return found


def discover(rel: str, payload: bytes) -> set[str]:
    """Extract further bundle paths referenced by a fetched js/css file."""
    found: set[str] = set()
    if rel.endswith(".css"):
        base_dir = rel.rsplit("/", 1)[0] if "/" in rel else ""
        for match in CSS_URL_RE.finditer(payload.decode("utf-8", "replace")):
            url = match.group(1)
            if url.startswith(("data:", "http:", "https:", "//")):
                continue
            found.add(f"{base_dir}/{url}" if base_dir else url)
        return found

    text = payload.decode("utf-8", "replace")
    for match in DIRECT_REF_RE.finditer(text):
        found.add(match.group(1))
    if "/runtime." in f"/{rel}":
        found.update(manifest_files(text))
    expanded: set[str] = set()
    for path in found:
        if "__LANG__" in path:
            expanded.update(path.replace("__LANG__", lang) for lang in LANGS)
        else:
            expanded.add(path)
    return expanded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE)
    args = parser.parse_args()

    seeds = ["charting_library.standalone.js", "charting_library.js"]
    queue = list(seeds)
    strict = set(seeds)
    done: set[str] = set()
    saved = skipped = 0

    while queue:
        rel = queue.pop()
        if rel in done or ".." in rel:
            continue
        done.add(rel)
        try:
            payload = fetch(args.base, rel)
        except Exception as exc:  # noqa: BLE001 — report and continue/skip
            if rel in strict:
                print(f"FATAL: {rel}: {exc}")
                return 1
            print(f"  skip (fetch failed): {rel}: {exc}")
            skipped += 1
            continue
        if looks_like_html(payload):
            if rel in strict:
                print(f"FATAL: {rel} returned HTML (SPA fallback) — path is wrong")
                return 1
            print(f"  skip (HTML fallback, chunk id likely internal): {rel}")
            skipped += 1
            continue

        target = DEST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        saved += 1

        if rel.endswith((".js", ".css")):
            for new_rel in discover(rel, payload):
                if new_rel not in done:
                    queue.append(new_rel)

    total_mb = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file()) / 1e6
    print(f"mirrored {saved} files ({total_mb:.1f} MB) into {DEST}; {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
