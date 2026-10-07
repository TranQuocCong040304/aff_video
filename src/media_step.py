"""Bước 2 (Voice & Media): parse script -> sinh audio từng đoạn (có cảm xúc,
đồng bộ ảnh-theo-câu khi có [[IMG:n]]) + tải ảnh thumbnail dự phòng, ghi ra
06_media_manifest.json để Bước 3 (video assembly) dùng.
"""
import json
from pathlib import Path

from .description import build_youtube_description
from .media import download_image
from .product_links import list_all_product_images
from .prompt_loader import render
from .script_parser import parse_segments
from .tts import DEFAULT_VOICE, synthesize_beats, synthesize_gemini
from .utils import match_by_name, slugify


def _relative_or_none(path, out_dir: Path):
    return str(path.relative_to(out_dir)) if path else None


# 1 lệnh gộp hết ~60 câu/video từng bị Groq cắt ngắn giữa chừng JSON (lỗi
# json_validate_failed thật đã gặp) — chia lô nhỏ hơn để output luôn đủ ngắn,
# tránh bị cắt.
_HIGHLIGHT_BATCH_SIZE = 20


def _extract_highlights(beat_texts: list[str], text_client) -> list[str]:
    """Vài lệnh gọi Groq cho toàn bộ video (chia lô ~_HIGHLIGHT_BATCH_SIZE câu/
    lần — gộp hết vào 1 lệnh từng bị cắt ngắn giữa chừng JSON khi video dài,
    xem lỗi thật đã gặp): với mỗi câu thoại, rút ra 1 cụm từ nhấn mạnh ngắn
    kiểu chữ to màu vàng/cam đè lên phụ đề (giống style CapCut phổ biến ở các
    video Top 5 affiliate) — xem prompts/04d_highlight.md. Lô nào lỗi thì bỏ
    qua trang trí cho riêng lô đó, không chặn cả video."""
    non_empty = [(i, t) for i, t in enumerate(beat_texts) if t.strip()]
    if not non_empty:
        return [""] * len(beat_texts)

    out = [""] * len(beat_texts)
    for batch_start in range(0, len(non_empty), _HIGHLIGHT_BATCH_SIZE):
        batch = non_empty[batch_start:batch_start + _HIGHLIGHT_BATCH_SIZE]
        indices, lines = zip(*batch)
        try:
            prompt = render(
                "04d_highlight.md",
                LINES_JSON=json.dumps(list(lines), ensure_ascii=False),
                COUNT=len(lines),
            )
            result = text_client.generate_json(prompt)
            highlights = result.get("highlights", [])
            if len(highlights) != len(lines):
                print(f"  [CẢNH BÁO] Groq trả {len(highlights)} highlight thay vì {len(lines)} cho 1 lô — bỏ qua trang trí lô này.")
                continue
            for idx, h in zip(indices, highlights):
                out[idx] = (h or "").strip()
        except Exception as e:
            print(f"  [CẢNH BÁO] Sinh chữ nhấn mạnh thất bại cho 1 lô: {e} — bỏ qua trang trí lô này.")
    return out


def run_media_step(
    script: str,
    selected_products: dict,
    out_dir: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    text_client=None,
    gemini_client=None,
) -> list:
    """`script` = nội dung 04_script.txt, `selected_products` = nội dung đã
    parse của 03_products_selected.json (dict có key "selected_products").
    Tách riêng khỏi PipelineResult để có thể chạy lại bước media sau khi
    người dùng sửa tay script, mà không cần gọi lại Gemini viết script.
    `gemini_client`: nếu có, hook/CTA sẽ dùng Gemini TTS (giọng có cảm xúc
    thật) thay vì edge-tts — CHỈ áp dụng cho hook/CTA vì Gemini TTS không trả
    timing từng câu nên không đồng bộ ảnh-theo-câu được (xem src/tts.py:
    synthesize_gemini). None thì toàn bộ vẫn dùng edge-tts như cũ."""
    segments = parse_segments(script)
    products_by_name = {
        p["name"]: p for p in selected_products.get("selected_products", [])
    }

    audio_dir = out_dir / "audio"
    manifest = []

    for seg in segments:
        product, base_name, blocks = plan_segment(seg, products_by_name, out_dir)

        audio_path = None
        beats = []
        has_text = any(b["text_with_tags"].strip() for b in blocks)
        if has_text:
            use_gemini = gemini_client is not None and seg.kind in ("hook", "cta")
            if use_gemini:
                gemini_path = audio_dir / f"{base_name}.wav"
                try:
                    beats = synthesize_gemini(blocks[0]["text_with_tags"], gemini_path, gemini_client)
                    audio_path = gemini_path
                except Exception as e:
                    print(f"[CẢNH BÁO] Gemini TTS thất bại cho '{base_name}': {e} — dùng edge-tts thay thế.")
                    use_gemini = False
            if not use_gemini:
                audio_path = audio_dir / f"{base_name}.mp3"
                try:
                    beats = synthesize_beats(blocks, audio_path, voice=voice, base_rate=rate)
                except Exception as e:
                    print(f"[CẢNH BÁO] Sinh audio thất bại cho '{base_name}': {e}")
                    audio_path = None
        else:
            print(f"[CẢNH BÁO] Đoạn '{base_name}' rỗng, bỏ qua TTS.")

        manifest.append(manifest_entry(seg, product, base_name, audio_path, beats, out_dir))

    return finish_manifest(manifest, out_dir, text_client)


def plan_segment(seg, products_by_name: dict, out_dir: Path):
    """(product, base_name, blocks) của 1 đoạn script. Mỗi block có thể gắn 1
    ảnh cụ thể (khi có [[IMG:n]]) để video hiện ĐÚNG ảnh lúc đọc câu mô tả
    ảnh đó. Dùng chung cho giọng máy và giọng tự thu (src/voice_import.py)."""
    product = match_by_name(seg.product_name or "", products_by_name) if seg.kind == "product" else None

    if seg.kind == "hook":
        base_name = "00_hook"
    elif seg.kind == "cta":
        base_name = "99_cta"
    else:
        rank = product.get("rank", 50 + seg.index) if product else 50 + seg.index
        base_name = f"{rank:02d}_{slugify(seg.product_name or 'sp')}"

    if seg.image_blocks and product:
        real_images = list_all_product_images(product, out_dir)
        blocks = []
        for b in seg.image_blocks:
            img = real_images[b["index"] - 1] if 0 < b["index"] <= len(real_images) else None
            blocks.append({
                "text_with_tags": b["raw_text"],
                "image": _relative_or_none(img, out_dir),
            })
    else:
        blocks = [{"text_with_tags": seg.raw_text, "image": None}]
    return product, base_name, blocks


def manifest_entry(seg, product, base_name: str, audio_path, beats: list, out_dir: Path) -> dict:
    # Ảnh thumbnail dự phòng (dùng khi 1 beat không gắn ảnh cụ thể nào, hoặc
    # cho sản phẩm không có product_media/ — xem src/video_assembly.py).
    image_path = None
    if seg.kind == "product":
        if product is None:
            print(
                f"[CẢNH BÁO] Không khớp được sản phẩm cho đoạn '{seg.product_name}' — "
                f"kiểm tra tên trong 03_products_selected.json. Bỏ qua ảnh."
            )
        elif product.get("image_url"):
            try:
                image_path = download_image(product["image_url"], out_dir / "images", base_name)
            except Exception as e:
                print(f"[CẢNH BÁO] Tải ảnh thất bại cho '{seg.product_name}': {e}")

    return {
        "order": seg.index,
        "kind": seg.kind,
        "product_name": seg.product_name,
        "audio_file": _relative_or_none(audio_path, out_dir),
        "image_file": _relative_or_none(image_path, out_dir),
        "text": seg.clean_text,
        "beats": beats,
    }


def finish_manifest(manifest: list, out_dir: Path, text_client=None) -> list:
    """Chữ nhấn mạnh + ghi 06_media_manifest.json + mô tả YouTube."""
    if text_client is not None:
        flat_beats = [b for seg in manifest for b in seg["beats"]]
        if flat_beats:
            print(f"Đang sinh chữ nhấn mạnh (highlight) cho {len(flat_beats)} câu (Groq, chia lô {_HIGHLIGHT_BATCH_SIZE} câu/lần)...")
            highlights = _extract_highlights([b["text"] for b in flat_beats], text_client)
            for beat, highlight in zip(flat_beats, highlights):
                beat["highlight"] = highlight

    (out_dir / "06_media_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Chỉ tính được mốc thời gian (timestamp) SAU KHI có audio thật, nên phần
    # mô tả YouTube đầy đủ (link + timestamp) được ghép ở đây — xem src/description.py.
    description = build_youtube_description(out_dir)
    if description:
        (out_dir / "08_youtube_description.txt").write_text(description, encoding="utf-8")

    # Đoạn thiếu audio bị video/CapCut bỏ qua hẳn (mất cả Top 1) — báo to
    # thay vì chỉ in cảnh báo ra terminal rồi coi như xong.
    missing = [seg["product_name"] or seg["kind"] for seg in manifest
               if seg["audio_file"] is None and (seg["text"] or "").strip()]
    if missing:
        raise RuntimeError(
            f"Không sinh được audio cho {len(missing)} đoạn: {'; '.join(m[:50] for m in missing)}. "
            "Video sẽ THIẾU các đoạn này — đợi vài phút (edge-tts đang chặn tạm) rồi bấm sinh audio lại."
        )

    return manifest
