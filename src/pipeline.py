"""Chuỗi prompt, chia làm 2 giai đoạn để chèn điểm dừng thủ công ở giữa, và
định tuyến 2 provider theo đúng việc mỗi bên làm tốt:

- `text_client` (Groq — free tier rộng rãi 30 rpm/~14.400 ngày): mọi bước chỉ
  cần text, không cần xem ảnh — ngách, lọc keyword, lọc sản phẩm, hook, CTA,
  metadata.
- `vision_client` (Gemini — multimodal, free tier hẹp 5 rpm/20 ngày): CHỈ bước
  viết lời bình từng sản phẩm, vì đây là bước duy nhất cần xem ảnh/video thật.

Nhờ vậy 1 video chỉ còn tốn ~5 request Gemini (thay vì 11) — vừa đúng bằng số
sản phẩm, phần còn lại dồn hết sang Groq.

Giai đoạn 1 (chọn lọc): Ngách -> Lọc keyword -> Lọc 5 sản phẩm.
    Dừng lại ở đây để người dùng vào link từng sản phẩm lấy ảnh/video chi tiết
    thật (product_media/<rank>_<slug>/) — xem src/product_links.py.

Giai đoạn 2 (viết script): Hook -> lời bình từng sản phẩm (kèm ảnh thật nếu có)
    -> CTA -> Metadata. Script được LẮP RÁP bằng code (không nhờ LLM tự gắn
    thẻ), nên tên sản phẩm trong "### SẢN PHẨM:" luôn khớp tuyệt đối với
    03_products_selected.json.
"""
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

from .models import Product
from .product_links import find_product_media
from .prompt_loader import render

_IMG_MARKER_RE = re.compile(r"\[\[IMG:(\d+)\]\]")


@dataclass
class SelectionResult:
    niche: dict
    filtered_keywords: dict
    selected_products: dict


@dataclass
class ScriptResult:
    script: str
    metadata: dict
    flow_prompts: dict  # {rank_str: prompt_text} — sản phẩm không có ảnh/video thật


def _keywords_to_bullet_list(keywords: List[dict]) -> str:
    lines = []
    for kw in keywords:
        parts = [kw["keyword"]]
        if kw.get("search_volume") is not None:
            parts.append(f"volume={kw['search_volume']}")
        if kw.get("competition") is not None:
            parts.append(f"competition={kw['competition']:.1f}")
        if kw.get("related_score") is not None:
            parts.append(f"related_score={kw['related_score']:.1f}")
        lines.append("- " + ", ".join(parts))
    return "\n".join(lines)


def _products_to_json(products: List[Product]) -> str:
    # Không indent: khoảng trắng thụt lề tốn token vô ích, đẩy prompt lọc sản
    # phẩm vượt giới hạn 8000 token/phút của Groq free tier.
    return json.dumps([p.to_dict() for p in products], ensure_ascii=False)


_SCORE_WEIGHTS = {"tinh_nang": 0.4, "danh_gia": 0.25, "gia_tri": 0.2, "lien_quan": 0.15}


def _price_of(p: dict) -> Optional[float]:
    try:
        return float(p["price"])
    except (KeyError, TypeError, ValueError):
        return None


def _rerank_by_price_ladder(selected_products: dict) -> dict:
    """Xếp hạng theo BẬC THANG GIÁ: TOP 1 = đắt/cao cấp nhất, TOP 5 = rẻ/bình
    dân nhất — video đếm ngược Top 5 -> Top 1 nên người xem đi từ bình dân lên
    cao cấp. Không tin thứ tự LLM trả về (hay lẫn rank), xếp lại bằng giá; giá
    bằng nhau thì sản phẩm điểm cao hơn đứng trên. Điểm tổng được tính lại từ
    điểm thành phần (LLM hay cộng/nhân sai) và chỉ dùng để CẢNH BÁO khi bậc
    thang bị ngược (hạng trên đắt hơn mà tính năng lại kém hạng dưới)."""
    products = selected_products.get("selected_products", [])

    def total(p):
        sc = p.get("scores") or {}
        try:
            if all(k in sc for k in _SCORE_WEIGHTS):
                return round(sum(float(sc[k]) * w for k, w in _SCORE_WEIGHTS.items()), 2)
            return float(p["score"])
        except (KeyError, TypeError, ValueError):
            return None

    for p in products:
        p["score"] = total(p)
    ordered = sorted(
        enumerate(products),
        key=lambda ip: (
            _price_of(ip[1]) is None,
            -(_price_of(ip[1]) or 0),
            -(ip[1]["score"] or 0),
            ip[1].get("rank") or ip[0] + 1,
        ),
    )
    products = [p for _, p in ordered]
    for i, p in enumerate(products, start=1):
        p["rank"] = i

    for upper, lower in zip(products, products[1:]):
        f_up = (upper.get("scores") or {}).get("tinh_nang")
        f_low = (lower.get("scores") or {}).get("tinh_nang")
        try:
            if f_up is not None and f_low is not None and float(f_up) < float(f_low):
                print(
                    f"  [CẢNH BÁO] TOP {upper['rank']} đắt hơn nhưng điểm tính năng ({f_up}) thấp hơn "
                    f"TOP {lower['rank']} ({f_low}) — nên chạy lại bước chọn sản phẩm hoặc sửa tay "
                    "03_products_selected.json."
                )
        except (TypeError, ValueError):
            pass
    if products and products[0].get("needs_missing"):
        print(
            f"  [CẢNH BÁO] TOP 1 vẫn còn thiếu nhu cầu: {products[0]['needs_missing']} — Top 1 nên đáp "
            "ứng được hết tiêu chí mà Top 5 -> Top 2 còn thiếu."
        )

    selected_products["selected_products"] = products
    return selected_products


def run_selection_stage(keywords: List[dict], products: List[Product], text_client) -> SelectionResult:
    """text_client: client chỉ cần generate_json() — dùng GroqClient (free tier rộng)."""
    prompt1 = render("01_ngach.md", KEYWORDS_LIST=_keywords_to_bullet_list(keywords))
    niche = text_client.generate_json(prompt1)

    prompt2 = render(
        "02_loc_keyword.md",
        NICHE_JSON=json.dumps(niche, ensure_ascii=False),
        KEYWORDS_LIST=_keywords_to_bullet_list(keywords),
    )
    filtered_keywords = text_client.generate_json(prompt2)

    prompt3 = render(
        "03_loc_san_pham.md",
        NICHE_JSON=json.dumps(niche, ensure_ascii=False),
        FILTERED_KEYWORDS_JSON=json.dumps(filtered_keywords, ensure_ascii=False),
        PRODUCTS_JSON=_products_to_json(products),
    )
    selected_products = _rerank_by_price_ladder(text_client.generate_json(prompt3))

    return SelectionResult(niche=niche, filtered_keywords=filtered_keywords, selected_products=selected_products)


def save_selection_result(result: SelectionResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "01_niche.json").write_text(
        json.dumps(result.niche, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "02_keywords_filtered.json").write_text(
        json.dumps(result.filtered_keywords, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "03_products_selected.json").write_text(
        json.dumps(result.selected_products, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _wrap_audio(text: str) -> str:
    return f"[Audio - Lời đọc]\n{text.strip()}\n[/Audio - Lời đọc]"


_PROGRESS_FILENAME = "04_script_progress.json"


def _progress_path(media_dir: Optional[Path]) -> Optional[Path]:
    return (media_dir / _PROGRESS_FILENAME) if media_dir else None


def _load_progress(media_dir: Optional[Path]) -> dict:
    path = _progress_path(media_dir)
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"hook": None, "segments": {}, "cta": None, "metadata": None}


def _save_progress(media_dir: Optional[Path], progress: dict) -> None:
    path = _progress_path(media_dir)
    if path:
        path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_progress(media_dir: Optional[Path]) -> None:
    path = _progress_path(media_dir)
    if path and path.exists():
        path.unlink()


def _build_media_instruction(images: list, video) -> str:
    """Ghép hướng dẫn về ảnh/video + yêu cầu định dạng khối [[IMG:n]] khi có
    ảnh — để mỗi câu lời bình gắn CHÍNH XÁC với 1 ảnh cụ thể (video xem lẫn
    tổng quan, không tách khối vì không thể đồng bộ theo khung hình)."""
    if not images:
        base = (
            "Chưa có ảnh chi tiết riêng cho sản phẩm này — chỉ dựa vào tên/giá/lý do được chọn ở "
            "trên để viết 3-5 câu liền mạch, không bịa chi tiết hình ảnh không có căn cứ."
        )
        if video:
            base += " Có đính kèm video sản phẩm, xem tổng quan để lấy thêm ngữ cảnh khi viết."
        return base

    n = len(images)
    lines = [
        f"Bạn được đính kèm {n} ảnh chụp thực tế theo ĐÚNG thứ tự dưới đây"
        + (" (kèm cả 1 video sản phẩm để xem thêm ngữ cảnh)" if video else "")
        + f". Với MỖI ảnh, viết 1-3 câu MÔ TẢ ĐÚNG những gì nhìn thấy trong CHÍNH ảnh đó (không mô "
        "tả nhầm sang ảnh khác), lồng ghép tự nhiên vào lời bình như đang vừa xem vừa kể.",
        "",
        f"Định dạng BẮT BUỘC — đúng {n} khối theo thứ tự, mỗi khối bắt đầu bằng đúng 1 dòng đánh dấu "
        "sau (không thêm bớt số khối, không viết gì khác trên dòng đánh dấu):",
    ]
    for i in range(1, n + 1):
        lines.append(f"[[IMG:{i}]]")
        lines.append(f"<lời bình cho ảnh {i}>")
    return "\n".join(lines)


def _validate_or_strip_image_blocks(response_text: str, expected_count: int, product_name: str) -> str:
    """Gemini đôi khi không tuân đúng định dạng [[IMG:n]] — nếu số khối không
    khớp, bỏ đồng bộ ảnh cho sản phẩm này (video assembly sẽ luân chuyển ảnh
    như bình thường) thay vì để marker rác lẫn vào lời bình."""
    if expected_count == 0:
        return _IMG_MARKER_RE.sub("", response_text).strip()
    found = [int(m.group(1)) for m in _IMG_MARKER_RE.finditer(response_text)]
    if found == list(range(1, expected_count + 1)):
        return response_text.strip()
    print(
        f"  [CẢNH BÁO] '{product_name[:40]}...': LLM không đánh dấu đúng {expected_count} khối ảnh "
        f"(tìm thấy {found or 'không có'}) — bỏ đồng bộ ảnh-theo-câu, dùng luân chuyển ảnh như cũ."
    )
    return _IMG_MARKER_RE.sub("", response_text).strip()


def _ranking_summary(products: List[dict]) -> str:
    """Bảng xếp hạng rút gọn cho prompt lời bình — để Gemini biết sản phẩm
    đang viết đứng trên/dưới ai và vì sao, tránh khen hạng thấp như Top 1."""
    rows = [
        {k: p.get(k) for k in ("rank", "tier", "name", "price", "needs_met", "needs_missing", "reason_selected",
                               "weakness") if p.get(k) is not None}
        for p in sorted(products, key=lambda x: x.get("rank") or 99)
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2)


def run_script_stage(
    niche: dict,
    selected_products: dict,
    vision_client,
    text_client,
    media_dir: Optional[Path] = None,
) -> ScriptResult:
    """vision_client: GeminiClient (bắt buộc hỗ trợ images/video) — dùng riêng cho
    từng đoạn sản phẩm. text_client: GroqClient — dùng cho hook/CTA/metadata."""
    products = selected_products.get("selected_products", [])
    if not products:
        raise ValueError("selected_products rỗng — không có sản phẩm nào để viết script.")

    progress = _load_progress(media_dir)

    if progress.get("hook"):
        print("  (dùng lại hook đã sinh từ lần chạy trước — bỏ qua, không tốn quota)")
        hook_text = progress["hook"]
    else:
        prompt_hook = render(
            "04a_hook.md",
            NICHE_JSON=json.dumps(niche, ensure_ascii=False),
            SELECTED_PRODUCTS_JSON=json.dumps(selected_products, ensure_ascii=False),
        )
        hook_text = text_client.generate_text(prompt_hook).strip()
        progress["hook"] = hook_text
        _save_progress(media_dir, progress)

    segment_blocks = []
    flow_prompts = dict(progress.get("flow_prompts", {}))
    # Đếm ngược TOP 5 -> TOP 1 (tiết lộ sản phẩm tốt nhất sau cùng để giữ chân
    # người xem) — đúng thứ tự phổ biến ở các video Top 5 affiliate, không
    # phải rank tăng dần như trước.
    products_countdown = sorted(products, key=lambda x: -(x.get("rank") or 0))
    for p in products_countdown:
        rank_key = str(p.get("rank"))
        images, video = find_product_media(p, media_dir) if media_dir else ([], None)

        if not images and not video and rank_key not in flow_prompts:
            prompt_flow = render(
                "06_flow_prompt.md",
                PRODUCT_JSON=json.dumps(p, ensure_ascii=False),
                NICHE_JSON=json.dumps(niche, ensure_ascii=False),
            )
            flow_prompts[rank_key] = text_client.generate_text(prompt_flow).strip()
            progress["flow_prompts"] = flow_prompts
            _save_progress(media_dir, progress)

        cached = progress.get("segments", {}).get(rank_key)
        if cached:
            print(f"  - Rank {p.get('rank')}: dùng lại lời bình đã sinh từ lần chạy trước — bỏ qua")
            seg_text = cached
        else:
            image_instruction = _build_media_instruction(images, video)
            prompt_seg = render(
                "04b_product_segment.md",
                NICHE_JSON=json.dumps(niche, ensure_ascii=False),
                PRODUCT_JSON=json.dumps(p, ensure_ascii=False),
                RANKING_JSON=_ranking_summary(products),
                RANK=p.get("rank", "?"),
                IMAGE_INSTRUCTION=image_instruction,
            )
            if images or video:
                media_desc = []
                if images:
                    media_desc.append(f"{len(images)} ảnh")
                if video:
                    media_desc.append(f"video '{video.name}'")
                print(f"  - Rank {p.get('rank')} ({p['name'][:40]}...): dùng {' + '.join(media_desc)} thật")
            raw_response = vision_client.generate_text(prompt_seg, images=images, video=video).strip()
            seg_text = _validate_or_strip_image_blocks(raw_response, len(images), p["name"])
            progress.setdefault("segments", {})[rank_key] = seg_text
            _save_progress(media_dir, progress)
        segment_blocks.append(f"### SẢN PHẨM: {p['name']}\n{_wrap_audio(seg_text)}")

    top_product = next((p for p in products if p.get("rank") == 1), products[0])

    if progress.get("cta"):
        print("  (dùng lại CTA đã sinh từ lần chạy trước — bỏ qua, không tốn quota)")
        cta_text = progress["cta"]
    else:
        prompt_cta = render(
            "04c_cta.md",
            NICHE_JSON=json.dumps(niche, ensure_ascii=False),
            TOP_PRODUCT_JSON=json.dumps(top_product, ensure_ascii=False),
        )
        cta_text = text_client.generate_text(prompt_cta).strip()
        progress["cta"] = cta_text
        _save_progress(media_dir, progress)

    script = "\n\n".join([_wrap_audio(hook_text), *segment_blocks, _wrap_audio(cta_text)])

    if progress.get("metadata"):
        print("  (dùng lại metadata đã sinh từ lần chạy trước — bỏ qua, không tốn quota)")
        metadata = progress["metadata"]
    else:
        hook_excerpt = hook_text[:400]
        prompt_meta = render(
            "05_metadata.md",
            CURRENT_YEAR=date.today().year,
            NICHE_JSON=json.dumps(niche, ensure_ascii=False),
            SELECTED_PRODUCTS_JSON=json.dumps(selected_products, ensure_ascii=False),
            SCRIPT_HOOK_EXCERPT=hook_excerpt,
        )
        metadata = text_client.generate_json(prompt_meta)
        progress["metadata"] = metadata
        _save_progress(media_dir, progress)

    _clear_progress(media_dir)
    return ScriptResult(script=script, metadata=metadata, flow_prompts=flow_prompts)


def save_script_result(result: ScriptResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "04_script.txt").write_text(result.script, encoding="utf-8")
    (out_dir / "05_metadata.json").write_text(
        json.dumps(result.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if result.flow_prompts:
        products_by_rank = {}  # điền tên sản phẩm cho dễ đọc nếu có sẵn 03_products_selected.json
        products_path = out_dir / "03_products_selected.json"
        if products_path.exists():
            data = json.loads(products_path.read_text(encoding="utf-8"))
            products_by_rank = {str(p["rank"]): p["name"] for p in data.get("selected_products", [])}

        lines = [
            "# Prompt Google Flow — cho sản phẩm chưa có ảnh/video thật\n",
            "Dán từng prompt bên dưới vào https://flow.google để tạo ảnh/video minh họa, rồi thả kết "
            "quả vào đúng thư mục `product_media/<rank>_<tên-sp>/main_images/` tương ứng và chạy lại "
            "`--script-only` để script bám theo ảnh/video đó.\n",
        ]
        for rank_key, prompt_text in sorted(result.flow_prompts.items(), key=lambda kv: int(kv[0])):
            name = products_by_rank.get(rank_key, f"Rank {rank_key}")
            lines.append(f"## TOP {rank_key}: {name}\n")
            lines.append(f"```\n{prompt_text}\n```\n")
        (out_dir / "07_flow_prompts.md").write_text("\n".join(lines), encoding="utf-8")
