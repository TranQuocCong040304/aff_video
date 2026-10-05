"""Sinh audio bằng Edge-TTS (miễn phí, không chính thức — xem rủi ro trong bản
kế hoạch: có thể ngừng hoạt động bất kỳ lúc nào không báo trước).

Mỗi KHỐI (block — ứng với 1 ảnh cụ thể, hoặc cả đoạn cho hook/CTA) được
synthesize bằng ĐÚNG 1 lệnh gọi API liên tục để giữ giọng đọc tự nhiên, liền
mạch. Phản hồi thật từ người dùng: tách nhiều lệnh gọi riêng cho từng thẻ cảm
xúc (thiết kế trước đây) dù cùng tên giọng vẫn nghe như đổi sang người khác ở
mỗi câu — mỗi lệnh gọi edge-tts là 1 lượt suy luận độc lập, không có gì đảm
bảo 2 lượt liên tiếp giữ đúng y hệt tông giọng/nhịp điệu.

Đã kiểm tra source code thư viện `edge_tts`: `Communicate` luôn bọc TOÀN BỘ
text trong 1 thẻ SSML `<prosody>` duy nhất (mkssml trong communicate.py), text
truyền vào bị escape trước khi chèn — nên KHÔNG có cách nào chèn nhiều mức
prosody khác nhau trong 1 request qua API công khai của thư viện.

Vì vậy: thẻ cảm xúc [cười nhẹ], [thở dài]... không còn đổi pitch/tốc độ nữa,
mà chỉ tạo KHOẢNG LẶNG gần đúng vị trí thẻ — bằng cách cắt audio đã
synthesize liền mạch (1 lệnh gọi cho cả block) tại ranh giới câu
(SentenceBoundary) gần nhất rồi chèn silence. Audio gốc không bị synthesize
lại nên giọng vẫn nhất quán 100% trong cùng 1 block.
"""
import asyncio
import re
import shutil
import time
import wave
from pathlib import Path
from typing import List, Optional, Tuple

import edge_tts
from moviepy import AudioClip, AudioFileClip, concatenate_audioclips

DEFAULT_VOICE = "vi-VN-HoaiMyNeural"  # giọng nữ. Giọng nam: vi-VN-NamMinhNeural

# Gemini TTS: dùng THAY edge-tts cho hook/CTA (không cần đồng bộ ảnh-theo-câu)
# vì model không trả timing từng câu — xem GeminiClient.generate_speech và
# src/media_step.py. Giọng có cảm xúc thật (đọc trực tiếp thẻ [hào hứng]...)
# thay vì chỉ tạo khoảng lặng như edge-tts.
DEFAULT_GEMINI_TTS_VOICE = "Kore"
_GEMINI_TTS_SAMPLE_RATE = 24000
_GEMINI_TTS_SAMPLE_WIDTH = 2  # 16-bit PCM

# Khoảng lặng (ms) chèn gần vị trí thẻ cảm xúc — khớp gần đúng theo từ khóa vì
# LLM có thể viết biến thể khác nhau (vd "cười tươi" thay vì "cười nhẹ").
_PAUSE_MS_RULES = [
    (("cười", "haha", "cười lớn", "cười tươi"), 150),
    (("nhấn mạnh", "khẳng định"), 90),
    (("thở dài", "buồn"), 320),
    (("ngập ngừng", "lưỡng lự", "băn khoăn"), 260),
    (("hào hứng", "phấn khích", "wow"), 110),
    (("gật gù", "đồng ý"), 140),
    (("thì thầm", "nhỏ nhẹ"), 130),
]
DEFAULT_PAUSE_MS = 90  # thẻ lạ: vẫn ngắt nhẹ dù không nhận diện được

_TAG_RE = re.compile(r"\[([^\]]*)\]")


def _match_pause_ms(tag_text: str) -> int:
    tag_text = tag_text.strip().lower()
    for keywords, ms in _PAUSE_MS_RULES:
        if any(kw in tag_text for kw in keywords):
            return ms
    return DEFAULT_PAUSE_MS


def _strip_tags_track_pauses(raw_text_with_tags: str) -> Tuple[str, List[Tuple[int, int]]]:
    """Bỏ thẻ cảm xúc khỏi text, trả về (text sạch để đọc, [(vị trí ký tự
    trong text sạch, pause_ms)]) — vị trí dùng để chèn khoảng lặng gần đúng
    chỗ thẻ từng xuất hiện."""
    clean_parts: List[str] = []
    pauses: List[Tuple[int, int]] = []
    last_end = 0
    for m in _TAG_RE.finditer(raw_text_with_tags):
        clean_parts.append(raw_text_with_tags[last_end:m.start()])
        char_pos = sum(len(p) for p in clean_parts)
        pauses.append((char_pos, _match_pause_ms(m.group(1))))
        last_end = m.end()
    clean_parts.append(raw_text_with_tags[last_end:])
    clean_text = "".join(clean_parts).strip()
    return clean_text, pauses


async def _synthesize_block_raw(text: str, out_path: Path, voice: str, rate: str) -> List[dict]:
    """1 lệnh gọi API duy nhất cho toàn bộ text của 1 block — audio liên tục,
    kèm timing từng câu (SentenceBoundary) để cắt/chèn khoảng lặng sau này."""
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    audio_bytes = bytearray()
    sentences: List[dict] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_bytes.extend(chunk["data"])
        elif chunk["type"] == "SentenceBoundary":
            sentences.append(
                {
                    "text": chunk["text"],
                    "start": chunk["offset"] / 10_000_000,
                    "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                }
            )
    if not audio_bytes:
        raise RuntimeError("edge-tts trả về audio rỗng.")
    out_path.write_bytes(bytes(audio_bytes))
    return sentences


def _synthesize_block_with_retry(
    text: str, out_path: Path, voice: str, rate: str, retries: int, retry_delay: float
) -> List[dict]:
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return asyncio.run(_synthesize_block_raw(text, out_path, voice, rate))
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(retry_delay)
    raise RuntimeError(f"edge-tts thất bại sau {retries} lần thử: {last_error}") from last_error


def _sentence_index_for_char(sentences: List[dict], char_pos: int) -> int:
    """Câu nào (theo thứ tự) chứa gần đúng vị trí ký tự char_pos — xấp xỉ
    bằng độ dài cộng dồn, đủ dùng để chèn khoảng lặng gần đúng chỗ."""
    cum = 0
    for i, s in enumerate(sentences):
        cum += len(s["text"]) + 1
        if char_pos <= cum:
            return i
    return len(sentences) - 1 if sentences else 0


def synthesize_beats(
    blocks: List[dict],
    out_path: Path,
    voice: str = DEFAULT_VOICE,
    base_rate: str = "+0%",
    retries: int = 5,
    retry_delay: float = 3.0,
    inter_block_delay: float = 0.6,
) -> List[dict]:
    """blocks: [{"text_with_tags": str, "image": Optional[str]}] — mỗi block
    ứng với 1 ảnh cụ thể (hoặc image=None nếu không gắn ảnh nào, vd hook/CTA).
    MỖI BLOCK = ĐÚNG 1 lệnh gọi edge-tts (giọng liền mạch trong cả block).

    Trả về beats: [{"text", "start", "end", "image"}] (giây) — mỗi câu trong
    block là 1 beat (để phụ đề tiến triển + đổi ảnh nếu cần), giữ nguyên
    "image" của block cha.
    """
    if not blocks:
        raise ValueError("Không có block nào để sinh audio.")

    tmp_dir = out_path.parent / f".{out_path.stem}_chunks"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    audio_segments = []  # clip đưa vào concatenate_audioclips cuối cùng
    block_audios = []  # AudioFileClip gốc của từng block — phải sống tới lúc export xong
    beats = []
    cursor = 0.0
    block_idx = 0
    try:
        for block in blocks:
            clean_text, pauses = _strip_tags_track_pauses(block.get("text_with_tags", ""))
            if not clean_text.strip():
                continue

            block_path = tmp_dir / f"{block_idx:03d}.mp3"
            if block_idx > 0 and inter_block_delay > 0:
                # Giãn nhẹ giữa các block — gọi liên tiếp không nghỉ dễ bị
                # edge-tts từ chối "No audio was received" (đã gặp thật, kể cả
                # sau 5 lần retry, vì retry cũng dồn dập không nghỉ).
                time.sleep(inter_block_delay)
            block_idx += 1
            sentences = _synthesize_block_with_retry(clean_text, block_path, voice, base_rate, retries, retry_delay)

            block_audio = AudioFileClip(str(block_path))
            block_audios.append(block_audio)

            if not sentences:
                # Text ngắn đôi khi không trả SentenceBoundary -> coi cả block là 1 câu.
                sentences = [{"text": clean_text, "start": 0.0, "end": block_audio.duration}]

            # Khoảng lặng cần chèn SAU câu nào (lấy pause lớn nhất nếu nhiều thẻ rơi vào cùng 1 câu).
            pause_after = [0] * len(sentences)
            for char_pos, pause_ms in pauses:
                idx = _sentence_index_for_char(sentences, char_pos)
                pause_after[idx] = max(pause_after[idx], pause_ms)

            for i, s in enumerate(sentences):
                start = max(0.0, min(s["start"], block_audio.duration))
                end = max(start, min(s["end"], block_audio.duration))
                if end <= start:
                    continue
                sub = block_audio.subclipped(start, end)
                audio_segments.append(sub)
                beats.append(
                    {
                        "text": s["text"],
                        "start": cursor,
                        "end": cursor + sub.duration,
                        "image": block.get("image"),
                    }
                )
                cursor += sub.duration

                if pause_after[i] > 0:
                    pause_s = pause_after[i] / 1000.0
                    silence = AudioClip(lambda t: 0, duration=pause_s, fps=24000)
                    audio_segments.append(silence)
                    cursor += pause_s

        if not audio_segments:
            raise ValueError("Không có mảnh text nào để sinh audio (toàn bộ block rỗng).")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        final_audio = concatenate_audioclips(audio_segments)
        final_audio.write_audiofile(str(out_path), logger=None)
        final_audio.close()
    finally:
        for c in audio_segments:
            try:
                c.close()
            except Exception:
                pass
        for c in block_audios:
            try:
                c.close()
            except Exception:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return beats


def synthesize(
    text_with_tags: str,
    out_path: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    retries: int = 5,
    retry_delay: float = 3.0,
) -> List[dict]:
    """Tiện ích cho đoạn KHÔNG gắn với ảnh cụ thể (hook/CTA, hoặc sản phẩm
    không có ảnh chi tiết) — 1 block duy nhất, image=None cho mọi beat."""
    if not text_with_tags.strip():
        raise ValueError("Không thể sinh audio từ text rỗng.")
    return synthesize_beats(
        [{"text_with_tags": text_with_tags, "image": None}],
        out_path, voice=voice, base_rate=rate, retries=retries, retry_delay=retry_delay,
    )


def synthesize_gemini(
    text_with_tags: str,
    out_path: Path,
    gemini_client,
    voice_name: str = DEFAULT_GEMINI_TTS_VOICE,
) -> List[dict]:
    """Sinh audio qua Gemini TTS (model gemini-3.1-flash-tts-preview) — CHỈ
    dùng cho đoạn không cần đồng bộ ảnh-theo-câu (hook/CTA, xem
    src/media_step.py). Model đọc trực tiếp thẻ cảm xúc [hào hứng]... như chỉ
    dẫn phong cách thật (không strip/chuyển thành khoảng lặng như edge-tts) —
    nhưng KHÔNG trả về timing từng câu, nên trả về ĐÚNG 1 beat cho cả đoạn."""
    pcm = gemini_client.generate_speech(text_with_tags, voice_name=voice_name)
    if not pcm:
        raise RuntimeError("Gemini TTS trả về audio rỗng.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(_GEMINI_TTS_SAMPLE_WIDTH)
        f.setframerate(_GEMINI_TTS_SAMPLE_RATE)
        f.writeframes(pcm)

    duration = len(pcm) / (_GEMINI_TTS_SAMPLE_RATE * _GEMINI_TTS_SAMPLE_WIDTH)
    clean_text = _TAG_RE.sub("", text_with_tags).strip()
    return [{"text": clean_text, "start": 0.0, "end": duration, "image": None}]
