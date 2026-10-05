"""Đọc dữ liệu đầu vào: CSV từ khóa (1 hoặc nhiều file) và JSON sản phẩm Shopee.

Đã cập nhật theo dữ liệu thật (khác giả định ban đầu):

- Từ khóa: export từ tool nghiên cứu keyword (vd "..._matching_terms.csv",
  "..._related_keywords.csv"), cột "Keyword", "Search volume", "Competition",
  "Overall", (tùy file) "Related score". Có thể có nhiều file cho cùng 1 ngách
  -> `load_keywords` nhận một thư mục và gộp tất cả *.csv trong đó, dedupe theo
  từ khóa, giữ lại các chỉ số nếu có.

- Sản phẩm: export từ extension scrape Shopee, dạng
  {"_meta": {...}, "searchQuery": "...", "products": [ {...} ]}. Mỗi sản phẩm
  có "productUrl" (KHÔNG phải link affiliate đã gắn tag), "price" dạng chuỗi
  "₫4.095.000", "salesVolume" dạng chuỗi "218 sold" / "1k sold".
"""
import csv
import json
import re
from pathlib import Path
from typing import List, Optional

from .models import Product

_NAME_KEYS = ("name", "product_name", "title")
_PRICE_KEYS = ("price", "product_price")
_IMAGE_KEYS = ("imageUrl", "image_url", "image", "thumbnail")
_LINK_KEYS = ("affiliate_link", "link", "url", "product_link")
_URL_ONLY_KEYS = ("productUrl",)  # link chưa gắn tag affiliate


def _first_present(d: dict, keys: tuple, required: bool, field_label: str):
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    if required:
        raise ValueError(
            f"Không tìm thấy trường '{field_label}' trong sản phẩm: {d}. "
            f"Đã thử các khóa: {keys}"
        )
    return None


def _parse_price(raw) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    digits = re.sub(r"[^\d]", "", str(raw))
    if not digits:
        raise ValueError(f"Không parse được giá tiền: {raw!r}")
    return float(digits)


def _parse_sold(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    m = re.search(r"([\d.,]+)\s*(k)?", str(raw), re.IGNORECASE)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    if m.group(2):
        num *= 1000
    return int(num)


def _parse_int(raw: Optional[str]) -> Optional[int]:
    if raw is None or str(raw).strip() == "":
        return None
    digits = re.sub(r"[^\d]", "", str(raw))
    return int(digits) if digits else None


def _parse_float(raw: Optional[str]) -> Optional[float]:
    if raw is None or str(raw).strip() == "":
        return None
    cleaned = re.sub(r"[^\d.]", "", str(raw))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def load_keywords(source: str | Path, max_keywords: int = 60) -> List[dict]:
    """Đọc 1 file CSV hoặc gộp tất cả *.csv trong 1 thư mục.

    Trả về list dict: {keyword, search_volume, competition, overall, related_score}
    đã dedupe và sắp xếp theo search_volume giảm dần, giới hạn `max_keywords` dòng
    để không làm prompt quá dài.
    """
    source = Path(source)
    if source.is_dir():
        csv_files = sorted(source.glob("*.csv"))
    elif source.is_file():
        csv_files = [source]
    else:
        raise FileNotFoundError(f"Không tìm thấy file/thư mục từ khóa: {source}")

    if not csv_files:
        raise FileNotFoundError(f"Không có file .csv nào trong: {source}")

    merged: dict[str, dict] = {}
    for path in csv_files:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = [c.strip() for c in (reader.fieldnames or [])]
            if "Keyword" not in fieldnames:
                raise ValueError(f"{path.name} cần có cột 'Keyword'.")
            for row in reader:
                kw_raw = row.get("Keyword")
                if not kw_raw or not kw_raw.strip():
                    continue
                keyword = kw_raw.strip()
                key = keyword.lower()
                entry = merged.setdefault(key, {"keyword": keyword})
                if entry.get("search_volume") is None:
                    entry["search_volume"] = _parse_int(row.get("Search volume"))
                if entry.get("competition") is None:
                    entry["competition"] = _parse_float(row.get("Competition"))
                if entry.get("overall") is None:
                    entry["overall"] = _parse_float(row.get("Overall"))
                related_score = _parse_float(row.get("Related score"))
                if related_score is not None:
                    entry["related_score"] = related_score

    keywords = list(merged.values())
    if not keywords:
        raise ValueError(f"Không đọc được từ khóa nào từ: {source}")
    keywords.sort(key=lambda k: (k.get("search_volume") or 0), reverse=True)
    return keywords[:max_keywords]


def load_products(json_path: str | Path, max_products: int = 30) -> List[Product]:
    json_path = Path(json_path)
    if not json_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file sản phẩm: {json_path}")

    raw = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "products" in raw:
        raw_products = raw["products"]
    elif isinstance(raw, list):
        raw_products = raw
    else:
        raise ValueError(
            "File JSON sản phẩm phải là một list, hoặc dict có khóa 'products' "
            "(định dạng export từ extension scrape Shopee)."
        )

    products: List[Product] = []
    warned_untagged_link = False
    for item in raw_products:
        price_raw = _first_present(item, _PRICE_KEYS, required=True, field_label="price")
        link = _first_present(item, _LINK_KEYS, required=False, field_label="affiliate_link")
        is_tagged = True
        if link is None:
            link = _first_present(item, _URL_ONLY_KEYS, required=True, field_label="affiliate_link/productUrl")
            is_tagged = False
            if not warned_untagged_link:
                print(
                    "[CẢNH BÁO] File sản phẩm không có link affiliate đã gắn tag — đang dùng "
                    "productUrl gốc. Cần chuyển các link này qua Shopee Affiliate Portal trước "
                    "khi đăng video, nếu không sẽ KHÔNG nhận được hoa hồng."
                )
                warned_untagged_link = True

        sold = item.get("sold")
        if sold is None:
            sold = _parse_sold(item.get("salesVolume"))

        products.append(
            Product(
                name=_first_present(item, _NAME_KEYS, required=True, field_label="name"),
                price=_parse_price(price_raw),
                image_url=_first_present(item, _IMAGE_KEYS, required=True, field_label="image_url"),
                affiliate_link=link,
                sold=sold,
                rating=item.get("rating"),
                discount=item.get("discount"),
                position=item.get("position"),
                is_tagged_affiliate_link=is_tagged,
            )
        )
    if not products:
        raise ValueError(f"File {json_path} không có sản phẩm nào.")

    if len(products) > max_products:
        # File nhiều sản phẩm (đã gặp thật: 52 sp) gửi hết vào prompt lọc top-5
        # (03_loc_san_pham.md) dễ vượt giới hạn token/phút của Groq (lỗi 413
        # "Request too large" — tài khoản free/mới giới hạn càng thấp). Ưu
        # tiên giữ lại sản phẩm bán chạy nhất trong giới hạn thay vì cắt theo
        # thứ tự file gốc, để không mất ứng viên tốt.
        print(
            f"[INFO] File có {len(products)} sản phẩm — chỉ dùng {max_products} sản phẩm bán chạy nhất "
            f"để tránh vượt giới hạn token/phút của Groq khi lọc top 5."
        )
        products = sorted(products, key=lambda p: p.sold or 0, reverse=True)[:max_products]

    return products
