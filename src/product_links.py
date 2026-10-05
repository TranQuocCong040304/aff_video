"""Xuất danh sách link 5 sản phẩm đã chọn + tạo thư mục để người dùng thả
main_images/description_images/video lấy từ trang sản phẩm Shopee vào — Gemini
sẽ nhìn trực tiếp các ảnh/video này khi viết script (xem src/pipeline.py:
run_script_stage).
"""
from pathlib import Path
from typing import Optional

from .utils import format_price, slugify

_README_TEXT = (
    "Thả media thật lấy từ trang sản phẩm Shopee vào đây, script sẽ được viết bám theo nội dung này:\n\n"
    "- main_images/         : ảnh chính từ trang sản phẩm\n"
    "- description_images/  : ảnh trong phần mô tả sản phẩm\n"
    "- product_video.*       : thả trực tiếp video sản phẩm vào thư mục này (không bắt buộc)\n\n"
    "Định dạng ảnh hỗ trợ: .jpg .jpeg .png .webp\n"
    "Định dạng video hỗ trợ: .mp4 .mov .webm .mkv .avi (Gemini xem trực tiếp qua Files API)\n"
    "Có thể để trống nếu không có — script vẫn viết được, chỉ dựa vào tên/giá/lý do được chọn.\n"
)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}

# Gemini giới hạn tổng dung lượng inline mỗi request (~20MB, ảnh mã hóa base64
# đội thêm ~33%) — chụp màn hình mô tả sản phẩm cộng dồn dễ vượt ngưỡng này
# (đã gặp thật: 1 sản phẩm 14 ảnh PNG ~14MB). Giới hạn dung lượng ảnh gửi mỗi
# sản phẩm, ưu tiên main_images trước, để không làm hỏng cả request giữa chừng.
_MAX_TOTAL_IMAGE_BYTES = 8 * 1024 * 1024

# Mỗi ảnh mô tả riêng -> 1 đoạn lời bình riêng, nên nhiều ảnh = script/video
# càng dài (đã gặp thật: 12 ảnh cho 1 sản phẩm làm video dài 10+ phút). Giới
# hạn số ảnh QUAN TRỌNG NHẤT gửi cho Gemini viết script — main_images luôn ưu
# tiên trước (thường là ảnh đại diện/ảnh chính do người bán chọn hiển thị đầu
# tiên), description_images bổ sung nếu còn chỗ.
_MAX_IMAGES_FOR_SCRIPT = 6


def _product_folder_name(product: dict) -> str:
    return f"{product['rank']:02d}_{slugify(product['name'])}"


def write_product_links_file(selected_products: dict, out_dir: Path) -> Path:
    lines = ["# 5 sản phẩm đã chọn — vào từng link để xem ảnh/video chi tiết\n"]
    for p in selected_products.get("selected_products", []):
        lines.append(f"## TOP {p['rank']}: {p['name']}")
        lines.append(f"- Giá: {format_price(p['price'])}")
        lines.append(f"- Link: {p['affiliate_link']}")
        if p.get("reason_selected"):
            lines.append(f"- Lý do chọn: {p['reason_selected']}")
        lines.append(f"- Thả media vào: `product_media/{_product_folder_name(p)}/` (xem README.txt trong đó)")
        lines.append("")

    path = out_dir / "03b_product_links.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def create_media_folders(selected_products: dict, out_dir: Path) -> list[Path]:
    base = out_dir / "product_media"
    folders = []
    for p in selected_products.get("selected_products", []):
        folder = base / _product_folder_name(p)
        (folder / "main_images").mkdir(parents=True, exist_ok=True)
        (folder / "description_images").mkdir(parents=True, exist_ok=True)
        readme = folder / "README.txt"
        if not readme.exists():
            readme.write_text(_README_TEXT, encoding="utf-8")
        folders.append(folder)
    return folders


def _product_folder(product: dict, out_dir: Path) -> Path:
    return out_dir / "product_media" / _product_folder_name(product)


def _images_in(sub: Path) -> list[Path]:
    return sorted(p for p in sub.glob("*") if p.suffix.lower() in _IMAGE_EXTS) if sub.exists() else []


def list_all_product_images(product: dict, out_dir: Path) -> list[Path]:
    """Toàn bộ ảnh thật của 1 sản phẩm, KHÔNG giới hạn dung lượng — dùng để cho
    ảnh luân chuyển trong video (khác với find_product_media dành cho Gemini,
    vốn phải giới hạn payload request)."""
    folder = _product_folder(product, out_dir)
    if not folder.exists():
        return []
    return (
        _images_in(folder / "main_images")
        + _images_in(folder / "description_images")
        + _images_in(folder)
    )


def find_product_media(product: dict, out_dir: Path) -> tuple[list[Path], Optional[Path]]:
    """Trả về (danh sách ảnh, 1 file video nếu có) cho 1 sản phẩm, dùng để gửi
    cho Gemini viết script — cắt bớt ảnh nếu tổng dung lượng vượt
    _MAX_TOTAL_IMAGE_BYTES (giới hạn payload request, không áp dụng khi dùng
    ảnh cho video — xem list_all_product_images)."""
    folder = _product_folder(product, out_dir)
    if not folder.exists():
        return [], None

    candidates = list_all_product_images(product, out_dir)
    if len(candidates) > _MAX_IMAGES_FOR_SCRIPT:
        print(
            f"  [INFO] '{product['name'][:40]}...' có {len(candidates)} ảnh — chỉ dùng "
            f"{_MAX_IMAGES_FOR_SCRIPT} ảnh quan trọng nhất (main_images trước) để video không quá dài."
        )
        candidates = candidates[:_MAX_IMAGES_FOR_SCRIPT]

    selected, total_bytes, skipped = [], 0, 0
    for p in candidates:
        size = p.stat().st_size
        if selected and total_bytes + size > _MAX_TOTAL_IMAGE_BYTES:
            skipped += 1
            continue
        selected.append(p)
        total_bytes += size
    if skipped:
        print(
            f"  [CẢNH BÁO] Sản phẩm '{product['name'][:40]}...' có ảnh vượt "
            f"{_MAX_TOTAL_IMAGE_BYTES // (1024*1024)}MB — bỏ bớt {skipped} ảnh cuối để tránh lỗi request quá lớn."
        )

    videos = sorted(p for p in folder.rglob("*") if p.suffix.lower() in _VIDEO_EXTS)
    return selected, (videos[0] if videos else None)
