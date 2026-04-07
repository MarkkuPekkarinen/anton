"""Publish HTML reports to the anton-services web host."""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from pathlib import Path

from anton.minds_http import minds_request


DEFAULT_PUBLISH_URL = "https://4eqbi9b2pg.execute-api.us-east-1.amazonaws.com/Prod"

# Patterns that capture relative paths from HTML attributes and CSS url()
_REF_PATTERNS = [
    re.compile(r'(?:src|href)\s*=\s*"([^":#?]+)"', re.IGNORECASE),
    re.compile(r"(?:src|href)\s*=\s*'([^':#?]+)'", re.IGNORECASE),
    re.compile(r'url\(\s*["\']?([^"\':#?)]+)["\']?\s*\)', re.IGNORECASE),
]


def _find_referenced_files(html_path: Path) -> list[Path]:
    """Scan an HTML file for relative references and return existing sibling paths."""
    try:
        html = html_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    parent = html_path.parent
    refs: set[Path] = set()

    for pattern in _REF_PATTERNS:
        for match in pattern.finditer(html):
            ref = match.group(1).strip()
            # Skip absolute URLs, data URIs, anchors, protocol-relative
            if not ref or ref.startswith(("/", "http:", "https:", "data:", "//")):
                continue
            candidate = (parent / ref).resolve()
            # Only include files that exist and are under the parent directory
            if candidate.is_file() and str(candidate).startswith(str(parent.resolve())):
                refs.add(candidate)

    return sorted(refs)


def _zip_html(path: Path) -> bytes:
    """Create a ZIP archive from an HTML file (with referenced siblings) or a directory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if path.is_file():
            zf.write(path, "index.html")
            # Bundle any referenced sibling files (JS, CSS, images, etc.)
            parent = path.resolve().parent
            for ref in _find_referenced_files(path):
                arc_name = str(ref.relative_to(parent))
                zf.write(ref, arc_name)
        else:
            # Directory — include all files
            for f in sorted(path.rglob("*")):
                if f.is_file():
                    zf.write(f, str(f.relative_to(path)))
    return buf.getvalue()


def publish(
    file_path: Path,
    *,
    api_key: str,
    publish_url: str = DEFAULT_PUBLISH_URL,
    ssl_verify: bool = True,
) -> dict:
    """Zip and upload an HTML file/directory. Returns the upload response dict.

    Response keys: user_prefix, md5, view_url, files
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Path not found: {file_path}")

    zipped = _zip_html(file_path)
    payload = json.dumps({"file_payload": base64.b64encode(zipped).decode()}).encode()

    url = f"{publish_url.rstrip('/')}/upload"
    raw = minds_request(url, api_key, method="POST", payload=payload, verify=ssl_verify)
    return json.loads(raw)
