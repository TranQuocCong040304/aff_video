"""Tải ảnh sản phẩm từ URL (link ảnh Shopee) về máy."""
import mimetypes
from pathlib import Path
from urllib.request import Request, urlopen

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def download_image(url: str, out_dir: Path, basename: str, timeout: int = 20) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers=_HEADERS)
    with urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        ext = mimetypes.guess_extension(content_type) or ".jpg"
        if ext == ".jpe":
            ext = ".jpg"
        data = resp.read()

    out_path = out_dir / f"{basename}{ext}"
    out_path.write_bytes(data)
    return out_path
