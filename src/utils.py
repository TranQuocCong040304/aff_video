import difflib
import re
import unicodedata
from typing import Optional


def format_price(price) -> str:
    return f"{int(round(price)):,} đ".replace(",", ".")


def slugify(text: str, max_len: int = 50) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len] or "sp"


def match_by_name(name: str, items_by_name: dict) -> Optional[dict]:
    """Khớp tên sản phẩm giữa 2 lần gọi Gemini khác nhau (script vs danh sách
    sản phẩm đã chọn) — có thể lệch hoa/thường hoặc khoảng trắng. Khớp chính
    xác trước, rồi mới fallback khớp gần đúng thay vì bỏ sót."""
    if name in items_by_name:
        return items_by_name[name]

    lower_map = {k.lower().strip(): v for k, v in items_by_name.items()}
    key = name.lower().strip()
    if key in lower_map:
        return lower_map[key]

    close = difflib.get_close_matches(key, lower_map.keys(), n=1, cutoff=0.75)
    return lower_map[close[0]] if close else None
