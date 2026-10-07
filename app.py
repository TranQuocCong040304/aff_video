"""Streamlit UI cho Auto Video Affiliate — bọc lại toàn bộ pipeline trong
main.py bằng giao diện web, để không phải nhớ các lệnh CLI.

Chạy:
    streamlit run app.py

Luồng trên UI giống hệt CLI, chỉ đổi cách thao tác:
    1) Dữ liệu đầu vào  -> upload CSV từ khóa + JSON sản phẩm vào data/input/
    2) Chọn sản phẩm     -> chạy Prompt 1-3 (Groq), chọn ĐÚNG 5 sản phẩm
    3) Ảnh sản phẩm      -> upload ảnh/video thật lấy từ Shopee cho từng sản phẩm
    4) Script & Audio    -> Gemini viết lời bình bám ảnh (Bước 2) + edge-tts
    5) Video             -> ghép MP4, xem trước, tải về
    6) Lịch sử           -> data/history.db, nhập số liệu hiệu suất sau khi đăng
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from src.database import list_runs, update_performance, log_run
from src.gemini_client import GeminiClient
from src.groq_client import GroqClient
from src.ingestion import load_keywords, load_products
from src.llm_errors import LLMJSONError, LLMQuotaExceededError, LLMServerError
from src.media_step import run_media_step
from src.pipeline import (
    run_script_stage,
    run_selection_stage,
    save_script_result,
    save_selection_result,
)
from src.product_links import (
    create_media_folders,
    list_all_product_images,
    write_product_links_file,
)
from src.tts import DEFAULT_VOICE
from src.voice_import import AUDIO_TYPES, import_recorded_voice, recording_script
from src.utils import slugify
from src.capcut_akp import (BLUR_LEVELS as AKP_BLUR_LEVELS, DEFAULT_OPTIONS as AKP_DEFAULTS,
                            KEYFRAME_STYLES as AKP_KEYFRAME_STYLES, SUBTITLE_STYLES as AKP_SUBTITLE_STYLES,
                            enhance_draft)
from src.capcut_export import capcut_is_running, default_drafts_root, export_capcut_draft
from src.capcut_style import describe_style, extract_style, list_styles, load_style, save_style
from src.render_job import any_render_running, get_status, start_render

load_dotenv()

# Neo theo thư mục chứa file này (không dùng cwd) — Streamlit có thể được khởi
# chạy từ thư mục khác (vd qua trình quản lý preview), nên "data/input" tương
# đối theo cwd sẽ sai chỗ nếu không neo cứng như thế này.
BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "data" / "input"
OUTPUT_DIR = BASE_DIR / "data" / "output"
DB_PATH = BASE_DIR / "data" / "history.db"
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

VOICES = {
    "Nữ - HoaiMy (mặc định)": "vi-VN-HoaiMyNeural",
    "Nam - NamMinh": "vi-VN-NamMinhNeural",
}

st.set_page_config(page_title="Auto Video Affiliate", page_icon="🎬", layout="wide")


# ---------------------------------------------------------------- helpers --

@st.cache_resource(show_spinner=False)
def _get_clients():
    """Khởi tạo 2 client 1 lần/phiên — constructor chỉ đọc .env, không gọi
    mạng nên cache an toàn. Trả về (vision_client, text_client, error)."""
    try:
        vision_client = GeminiClient()
    except RuntimeError as e:
        return None, None, f"[LỖI CẤU HÌNH - GEMINI] {e}"
    try:
        text_client = GroqClient()
    except RuntimeError as e:
        return None, None, f"[LỖI CẤU HÌNH - GROQ] {e}"
    return vision_client, text_client, None


def list_output_runs() -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    return sorted((p for p in OUTPUT_DIR.iterdir() if p.is_dir()), reverse=True)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def run_stage_flags(run_dir: Path) -> dict:
    return {
        "selection": (run_dir / "03_products_selected.json").exists(),
        "script": (run_dir / "04_script.txt").exists(),
        "media": (run_dir / "06_media_manifest.json").exists(),
        "video": (run_dir / "final_video.mp4").exists(),
    }


def product_folder_name(product: dict) -> str:
    return f"{product['rank']:02d}_{slugify(product['name'])}"


def _log_run_to_db(run_dir: Path, video_path: Path) -> None:
    niche = load_json(run_dir / "01_niche.json")
    metadata = load_json(run_dir / "05_metadata.json")
    products = load_json(run_dir / "03_products_selected.json")
    log_run(
        run_dir.name,
        niche.get("niche") if niche else None,
        metadata.get("title") if metadata else None,
        str(video_path),
        [p["name"] for p in products.get("selected_products", [])] if products else [],
        db_path=DB_PATH,
    )


# --------------------------------------------------------------- sidebar --

st.sidebar.title("🎬 Auto Video Affiliate")

existing_runs = list_output_runs()
run_options = ["➕ Tạo run mới"] + [p.name for p in existing_runs]
choice = st.sidebar.selectbox("Chọn phiên làm việc (run)", run_options)

if choice == "➕ Tạo run mới":
    run_dir = None
else:
    run_dir = OUTPUT_DIR / choice

st.sidebar.caption(
    "Mỗi lần chọn sản phẩm mới sẽ tạo 1 run (thư mục timestamp trong data/output/). "
    "Chọn lại run cũ ở đây để tiếp tục dở (upload thêm ảnh, viết lại script, ghép lại video...)."
)

vision_client, text_client, client_err = _get_clients()
if client_err:
    st.sidebar.error(client_err)
    st.sidebar.caption("Điền GEMINI_API_KEY / GROQ_API_KEY vào file .env rồi tải lại trang.")

tab_input, tab_select, tab_media, tab_script, tab_video, tab_history = st.tabs(
    ["📥 Dữ liệu đầu vào", "🎯 Chọn sản phẩm", "🖼️ Ảnh sản phẩm", "✍️ Script & Audio", "🎬 Video", "📊 Lịch sử"]
)


# ------------------------------------------------------------ tab: input --

with tab_input:
    st.subheader("Dữ liệu đầu vào (data/input/)")
    st.caption(
        "Cần: 1+ file CSV từ khóa (export từ tool nghiên cứu keyword, bắt buộc có cột 'Keyword') "
        "và 1 file JSON sản phẩm Shopee (dạng {\"products\": [...]})."
    )

    current_files = sorted(INPUT_DIR.glob("*"))
    if current_files:
        st.write("File hiện có trong `data/input/`:")
        for f in current_files:
            if f.name != ".gitkeep":
                col1, col2 = st.columns([5, 1])
                col1.write(f"- `{f.name}` ({f.stat().st_size // 1024} KB)")
                if col2.button("Xóa", key=f"del_input_{f.name}"):
                    f.unlink()
                    st.rerun()
    else:
        st.info("Chưa có file nào.")

    uploaded = st.file_uploader(
        "Thả file CSV từ khóa / JSON sản phẩm vào đây",
        type=["csv", "json"],
        accept_multiple_files=True,
    )
    if uploaded:
        if st.button("Lưu vào data/input/"):
            for f in uploaded:
                (INPUT_DIR / f.name).write_bytes(f.getvalue())
            st.success(f"Đã lưu {len(uploaded)} file.")
            st.rerun()


# ---------------------------------------------------------- tab: select --

with tab_select:
    st.subheader("Giai đoạn 1 — Chọn ngách + lọc đúng 5 sản phẩm (Groq)")

    if run_dir is not None:
        selection_data = load_json(run_dir / "03_products_selected.json")
        niche_data = load_json(run_dir / "01_niche.json")
        if selection_data:
            st.success(f"Run `{run_dir.name}` đã chọn xong sản phẩm.")
            if niche_data:
                st.write(f"**Ngách:** {niche_data.get('niche')}")
            for p in selection_data.get("selected_products", []):
                tier = f" ({p['tier']})" if p.get("tier") else ""
                with st.expander(f"TOP {p['rank']}{tier}: {p['name']}"):
                    st.write(f"Giá: {p['price']:,.0f}đ" if isinstance(p.get("price"), (int, float)) else p.get("price"))
                    st.write(f"Link: {p['affiliate_link']}")
                    if p.get("reason_selected"):
                        st.write(f"Lý do chọn: {p['reason_selected']}")
                    if p.get("needs_met"):
                        st.write(f"Đáp ứng: {', '.join(p['needs_met'])}")
                    if p.get("needs_missing"):
                        st.write(f"Còn thiếu: {', '.join(p['needs_missing'])}")
                    if p.get("nitpick"):
                        st.write(f"Chê nhẹ: {p['nitpick']}")
        else:
            st.warning("Run này chưa có kết quả chọn sản phẩm.")
    else:
        st.write("Sẽ đọc toàn bộ CSV/JSON trong `data/input/` và tạo 1 run mới.")
        csv_files = list(INPUT_DIR.glob("*.csv"))
        json_files = list(INPUT_DIR.glob("*.json"))
        st.caption(f"Tìm thấy {len(csv_files)} file CSV, {len(json_files)} file JSON trong data/input/.")

        disabled = client_err is not None or not csv_files or len(json_files) != 1
        if len(json_files) > 1:
            st.error(f"Có {len(json_files)} file JSON trong data/input/ — chỉ được để đúng 1 file sản phẩm.")
        elif not csv_files or not json_files:
            st.error("Thiếu file CSV từ khóa hoặc JSON sản phẩm — thêm ở tab 'Dữ liệu đầu vào'.")

        if st.button("🚀 Chạy chọn sản phẩm (Prompt 1-3)", disabled=disabled, type="primary"):
            try:
                keywords = load_keywords(INPUT_DIR)
                products = load_products(json_files[0])
            except (FileNotFoundError, ValueError) as e:
                st.error(f"[LỖI DỮ LIỆU ĐẦU VÀO] {e}")
                st.stop()

            with st.spinner(f"Đang chọn ngách + lọc 5 sản phẩm từ {len(keywords)} từ khóa, {len(products)} sản phẩm..."):
                try:
                    selection = run_selection_stage(keywords, products, text_client)
                except (LLMJSONError, LLMQuotaExceededError, LLMServerError) as e:
                    st.error(f"[LỖI] {e}")
                    st.stop()

            new_run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_run_dir = OUTPUT_DIR / new_run_id
            save_selection_result(selection, new_run_dir)
            write_product_links_file(selection.selected_products, new_run_dir)
            create_media_folders(selection.selected_products, new_run_dir)
            st.success(f"Đã tạo run `{new_run_id}` — chọn nó ở sidebar để tiếp tục sang tab 'Ảnh sản phẩm'.")
            st.rerun()


# ----------------------------------------------------------- tab: media --

with tab_media:
    st.subheader("Ảnh/video thật của 5 sản phẩm (thả vào để script bám theo)")
    if run_dir is None:
        st.info("Chọn hoặc tạo 1 run ở tab 'Chọn sản phẩm' trước.")
    else:
        selection_data = load_json(run_dir / "03_products_selected.json")
        if not selection_data:
            st.warning("Run này chưa chọn sản phẩm.")
        else:
            st.caption(
                "Vào từng link Shopee bên dưới, tải ảnh chính/ảnh mô tả/video sản phẩm về máy, "
                "rồi upload lại đây — Gemini sẽ viết lời bình bám theo ĐÚNG những ảnh này "
                "(tối đa 6 ảnh quan trọng nhất/sản phẩm được gửi cho Gemini)."
            )
            for p in selection_data.get("selected_products", []):
                folder = run_dir / "product_media" / product_folder_name(p)
                (folder / "main_images").mkdir(parents=True, exist_ok=True)
                (folder / "description_images").mkdir(parents=True, exist_ok=True)
                current_images = list_all_product_images(p, run_dir)
                current_videos = [v for v in folder.glob("*") if v.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}]

                with st.expander(f"TOP {p['rank']}: {p['name']} — đã có {len(current_images)} ảnh, {len(current_videos)} video", expanded=False):
                    st.markdown(f"[Mở link sản phẩm trên Shopee]({p['affiliate_link']})")

                    main_up = st.file_uploader(
                        "Ảnh chính (main_images)", type=["jpg", "jpeg", "png", "webp"],
                        accept_multiple_files=True, key=f"main_{p['rank']}",
                    )
                    desc_up = st.file_uploader(
                        "Ảnh mô tả (description_images)", type=["jpg", "jpeg", "png", "webp"],
                        accept_multiple_files=True, key=f"desc_{p['rank']}",
                    )
                    video_up = st.file_uploader(
                        "Video sản phẩm (không bắt buộc)", type=["mp4", "mov", "webm", "mkv", "avi"],
                        accept_multiple_files=False, key=f"video_{p['rank']}",
                    )

                    if st.button("💾 Lưu media đã upload", key=f"save_media_{p['rank']}"):
                        n = 0
                        for f in main_up or []:
                            (folder / "main_images" / f.name).write_bytes(f.getvalue())
                            n += 1
                        for f in desc_up or []:
                            (folder / "description_images" / f.name).write_bytes(f.getvalue())
                            n += 1
                        if video_up is not None:
                            (folder / video_up.name).write_bytes(video_up.getvalue())
                            n += 1
                        st.success(f"Đã lưu {n} file cho '{p['name'][:40]}'.")
                        st.rerun()

                    if current_images:
                        st.image([str(i) for i in current_images[:6]], width=120)


# ---------------------------------------------------------- tab: script --

with tab_script:
    st.subheader("Giai đoạn 2 — Script (Gemini, bám ảnh thật) + Audio (edge-tts)")
    if run_dir is None:
        st.info("Chọn hoặc tạo 1 run trước.")
    else:
        flags = run_stage_flags(run_dir)
        selection_data = load_json(run_dir / "03_products_selected.json")
        niche_data = load_json(run_dir / "01_niche.json")

        if not selection_data or not niche_data:
            st.warning("Run này chưa chọn sản phẩm.")
        else:
            col1, col2 = st.columns(2)
            gen_label = "🔁 Viết lại script" if flags["script"] else "✍️ Viết script (gọi Gemini)"
            if col1.button(gen_label, disabled=client_err is not None):
                progress_note = st.empty()
                with st.spinner("Đang viết lời bình từng sản phẩm bám ảnh thật (mỗi sản phẩm ~1 request Gemini)..."):
                    try:
                        script_result = run_script_stage(
                            niche_data, selection_data, vision_client, text_client, media_dir=run_dir
                        )
                    except (LLMJSONError, LLMQuotaExceededError, LLMServerError) as e:
                        st.error(f"[LỖI] {e}")
                        if (run_dir / "04_script_progress.json").exists():
                            st.warning(
                                "Tiến độ đã hoàn thành được lưu lại — bấm lại nút này sau, "
                                "tool sẽ bỏ qua phần đã xong, không tốn quota làm lại."
                            )
                        st.stop()
                save_script_result(script_result, run_dir)
                st.success("Đã viết xong script + metadata.")
                st.rerun()

            script_path = run_dir / "04_script.txt"
            if script_path.exists():
                script_text = script_path.read_text(encoding="utf-8")
                edited = st.text_area("04_script.txt (có thể sửa tay trước khi sinh audio)", script_text, height=400)
                if col2.button("💾 Lưu chỉnh sửa script"):
                    script_path.write_text(edited, encoding="utf-8")
                    st.success("Đã lưu.")

                flow_path = run_dir / "07_flow_prompts.md"
                if flow_path.exists():
                    with st.expander("⚠️ Có sản phẩm thiếu ảnh thật — prompt Google Flow để tự tạo ảnh AI bù vào"):
                        st.markdown(flow_path.read_text(encoding="utf-8"))

                metadata = load_json(run_dir / "05_metadata.json")
                if metadata:
                    with st.expander("05_metadata.json (tiêu đề/mô tả/tags gợi ý cho YouTube)"):
                        st.json(metadata)

                st.divider()
                voice_source = st.radio(
                    "Giọng đọc", ["🤖 Giọng máy (edge-tts)", "🎙️ Tự thu (Google Vids, ElevenLabs, tự đọc...)"],
                    horizontal=True,
                    help="edge-tts chỉ có 2 giọng tiếng Việt. Tự thu: lấy kịch bản bên dưới, thu ở đâu cũng được "
                         "rồi nộp lại — tool tự nghe lại để khớp ảnh/phụ đề theo từng câu.",
                )
                if voice_source.startswith("🤖"):
                    voice_label = st.selectbox("Giọng", list(VOICES.keys()))
                    rate = st.text_input("Tốc độ đọc (vd +0%, +10%, -5%)", "+0%")
                    if st.button("🔊 Sinh audio từ script hiện tại"):
                        script_path.write_text(edited, encoding="utf-8")
                        with st.spinner("Đang sinh audio từng đoạn (edge-tts) + tải ảnh thumbnail dự phòng..."):
                            try:
                                run_media_step(edited, selection_data, run_dir, voice=VOICES[voice_label], rate=rate,
                                               text_client=text_client)
                            except Exception as e:
                                st.error(f"[LỖI] {e}")
                                st.stop()
                        st.success("Đã sinh xong audio — sang tab 'Video' để ghép.")
                        st.rerun()
                else:
                    rec_script = recording_script(edited, selection_data)
                    st.markdown(
                        "**Bước 1** — tải kịch bản thu âm (đã bỏ thẻ cảm xúc và `[[IMG]]`). Ở Google Vids: mỗi đoạn "
                        "dán vào 1 cảnh → *Voiceover* → chọn giọng → tải video (mp4) về."
                    )
                    st.download_button("📄 Tải kịch bản thu âm (.txt)", rec_script,
                                       file_name=f"kich_ban_thu_am_{run_dir.name}.txt", mime="text/plain")
                    with st.expander("Xem kịch bản thu âm"):
                        st.text(rec_script)
                    st.markdown(
                        "**Bước 2** — nộp audio: **1 file liền cả bài**, hoặc **nhiều file** (mỗi đoạn 1 file — tool "
                        "nối theo thứ tự tên file, nên đặt 01, 02, 03...). Nhận cả mp4 tải từ Google Vids."
                    )
                    uploads = st.file_uploader("Audio tự thu", type=AUDIO_TYPES, accept_multiple_files=True)
                    if st.button("🎙️ Dùng giọng tự thu", disabled=not uploads or client_err is not None,
                                 help=client_err):
                        script_path.write_text(edited, encoding="utf-8")
                        upload_dir = run_dir / "voice_upload"
                        shutil.rmtree(upload_dir, ignore_errors=True)
                        upload_dir.mkdir(parents=True)
                        files = []
                        for up in sorted(uploads, key=lambda u: u.name):
                            dest = upload_dir / up.name
                            dest.write_bytes(up.getvalue())
                            files.append(dest)
                        with st.spinner("Đang nối audio, nghe lại bằng Whisper (Groq) để khớp từng câu với script..."):
                            try:
                                report = import_recorded_voice(edited, selection_data, run_dir, files,
                                                               groq_client=text_client, text_client=text_client)
                            except Exception as e:
                                st.error(f"[LỖI] {type(e).__name__}: {e}")
                                st.stop()
                        st.session_state["voice_report"] = {"run": run_dir.name, **report}
                        st.rerun()
                    report = st.session_state.get("voice_report")
                    if report and report["run"] == run_dir.name:
                        st.success(
                            f"Đã dùng giọng tự thu ({report['duration'] / 60:.1f} phút, khớp {report['coverage']:.0%} "
                            "số từ của script) — sang tab 'Video' để ghép."
                        )
                        for seg in report["segments"]:
                            line = f"- `{seg['name']}`: {seg['duration']}s — khớp {seg['coverage']:.0%}"
                            if seg["coverage"] < 0.7:
                                line += " ⚠️ đọc lệch script nhiều, ảnh/phụ đề đoạn này có thể lệch — nghe lại đoạn này"
                            st.markdown(line)

                if flags["media"]:
                    st.success("Đã có audio (06_media_manifest.json) — sang tab 'Video' để ghép MP4.")
                    audio_dir = run_dir / "audio"
                    if audio_dir.exists():
                        for f in sorted(p for p in audio_dir.glob("*.mp3") if not p.name.startswith("_")):
                            st.caption(f.name)
                            st.audio(str(f))

                    st.divider()
                    st.write("**📄 Mô tả YouTube** (tiêu đề, link sản phẩm, mốc thời gian, hashtag — tự ghép sau khi có audio)")
                    desc_path = run_dir / "08_youtube_description.txt"
                    if desc_path.exists():
                        desc_text = st.text_area(
                            "08_youtube_description.txt (dán trực tiếp vào ô mô tả video khi đăng YouTube)",
                            desc_path.read_text(encoding="utf-8"), height=350, key="youtube_desc",
                        )
                        if st.button("💾 Lưu chỉnh sửa mô tả"):
                            desc_path.write_text(desc_text, encoding="utf-8")
                            st.success("Đã lưu.")
                        st.caption(
                            "⚠️ Link trong mô tả lấy từ 03_products_selected.json — nhớ đổi sang link đã gắn tag "
                            "affiliate (Shopee Affiliate Portal) trước khi đăng thật, nếu không sẽ không nhận hoa hồng."
                        )
                    else:
                        st.info("Chưa có mô tả — sinh lại audio ở trên để tạo (cần audio để tính mốc thời gian).")


# ----------------------------------------------------------- tab: video --

@st.fragment(run_every=3)
def _render_progress(run_dir: Path) -> None:
    """Tự làm mới mỗi 3s, chỉ phần tiến độ (không chạy lại cả trang). Việc
    ghép chạy ở thread nền (src/render_job.py) nên F5/đóng tab không làm mất
    kết quả — mở lại trang là thấy tiến độ tiếp."""
    status = get_status(run_dir) or {}
    if status.get("state") != "running":
        st.rerun(scope="app")  # vừa xong/lỗi -> vẽ lại cả trang để hiện video
    progress = float(status.get("progress") or 0.0)
    elapsed = ""
    if status.get("started_at"):
        secs = int((datetime.now() - datetime.fromisoformat(status["started_at"])).total_seconds())
        elapsed = f" — đã chạy {secs // 60} phút {secs % 60:02d} giây"
    st.progress(min(progress, 1.0), text=f"⏳ {status.get('phase', 'Đang ghép')}: {progress:.0%}{elapsed}")
    st.caption("Đang ghép ở chế độ nền — có thể tải lại trang hoặc sang tab khác, video vẫn tiếp tục được ghép.")


with tab_video:
    st.subheader("Giai đoạn 3 — Xuất video MP4 / project CapCut")
    if run_dir is None:
        st.info("Chọn hoặc tạo 1 run trước.")
    else:
        flags = run_stage_flags(run_dir)
        if not flags["media"]:
            st.warning("Run này chưa có audio (06_media_manifest.json) — làm ở tab 'Script & Audio' trước.")
        else:
            no_audio = [s.get("product_name") or s["kind"] for s in load_json(run_dir / "06_media_manifest.json") or []
                        if not s.get("audio_file") and (s.get("text") or "").strip()]
            if no_audio:
                st.error(
                    f"⚠️ {len(no_audio)} đoạn chưa có audio nên sẽ bị BỎ khỏi video/CapCut: "
                    + "; ".join(n[:50] for n in no_audio)
                    + " — quay lại tab 'Script & Audio' bấm sinh audio lại."
                )
            output = st.radio(
                "Đầu ra", ["🎬 Video MP4", "📤 Project CapCut", "🎬 + 📤 Cả hai"], horizontal=True,
                help="Video MP4: tool tự ghép xong là đăng được. Project CapCut: mở CapCut biên tập tiếp rồi "
                     "Export ở đó. Cả hai: tạo project CapCut trước (vài giây) rồi ghép MP4 ở chế độ nền.",
            )
            want_mp4 = output != "📤 Project CapCut"
            want_capcut = output != "🎬 Video MP4"

            vertical = st.checkbox("Dọc (Shorts 1080x1920)", value=False)
            canvas = (1080, 1920) if vertical else (1920, 1080)

            if want_mp4:
                st.markdown("**Video MP4**")
                col2, col3 = st.columns(2)
                fps = col2.number_input("FPS", min_value=15, max_value=60, value=30)
                font_path_input = col3.text_input("Font .ttf tùy chỉnh (bỏ trống = mặc định)", "")

            if want_capcut:
                st.markdown("**Project CapCut**")
                st.caption(
                    "Tạo sẵn 1 project CapCut (ảnh/video theo từng câu, giọng đọc, phụ đề, badge TOP/giá) — "
                    "mở CapCut là thấy, thêm hiệu ứng/chuyển cảnh rồi Export ở đó."
                )
                if capcut_is_running():
                    st.warning("CapCut đang mở — nên đóng CapCut trước khi xuất để project mới hiện trong danh sách.")
                style_names = list_styles()
                col_name, col_style = st.columns(2)
                capcut_name = col_name.text_input("Tên project CapCut", f"AutoVideo {run_dir.name}")
                style_choice = col_style.selectbox(
                    "Bộ phong cách", ["(Mặc định)"] + style_names,
                    index=1 if style_names else 0,
                    help="Mỗi kênh nên dùng 1 bộ phong cách riêng — tạo ở mục 'Lưu phong cách mới' bên dưới.",
                )
                chosen_style = load_style(style_choice) if style_choice in style_names else None
                if chosen_style:
                    with st.expander(f"Bộ phong cách '{style_choice}' gồm"):
                        for line in describe_style(chosen_style):
                            st.markdown(f"- {line}")
                use_akp = st.checkbox(
                    "✨ Làm đẹp bằng Auto Keyframe Pro", value=True,
                    help="Add-on của Tool AutoCapcut V6.3: keyframe có gia tốc (ease), lớp nền mờ + Canvas Blur, "
                         "video dọc để vừa khung. Tự backup, chạy lại không chồng lớp.",
                )
                akp_opts = {}
                if use_akp:
                    with st.expander("Tuỳ chọn Auto Keyframe Pro"):
                        c1, c2 = st.columns(2)
                        akp_opts["style"] = c1.selectbox(
                            "Kiểu chuyển động", AKP_KEYFRAME_STYLES,
                            index=AKP_KEYFRAME_STYLES.index(AKP_DEFAULTS["style"]),
                        )
                        akp_opts["bg_blur"] = c2.selectbox(
                            "Độ mờ lớp nền", AKP_BLUR_LEVELS, index=AKP_BLUR_LEVELS.index(AKP_DEFAULTS["bg_blur"]),
                        )
                        akp_opts["zoom"] = c1.slider("Mức zoom", 1.02, 1.30, float(AKP_DEFAULTS["zoom"]), 0.01)
                        akp_opts["bg_alpha"] = c2.slider("Độ đậm lớp nền", 0.10, 1.00, float(AKP_DEFAULTS["bg_alpha"]), 0.05)
                        akp_opts["keyword_subs"] = st.checkbox(
                            "Phụ đề TỪ KHOÁ thay phụ đề nguyên câu",
                            help="Mỗi clip ảnh chỉ hiện 1 cụm từ khoá (số liệu, mã máy, danh từ chính). "
                                 "Phụ đề gốc bị ẩn, không xoá; badge TOP/giá giữ nguyên.",
                        )
                        if akp_opts["keyword_subs"]:
                            akp_opts["sub_style"] = st.selectbox("Kiểu chữ từ khoá", AKP_SUBTITLE_STYLES)
                        c3, c4 = st.columns(2)
                        akp_opts["letterbox"] = c3.checkbox("Khung 19:6 (letterbox)")
                        akp_opts["vignette"] = c4.checkbox("Vignette 2 mép")

            status = get_status(run_dir)
            running_other = any_render_running()
            is_running = bool(status and status.get("state") == "running")
            no_drafts_root = want_capcut and default_drafts_root() is None
            if no_drafts_root:
                block_reason = "Không tìm thấy thư mục draft CapCut — đặt CAPCUT_DRAFT_DIR trong .env"
            elif want_mp4 and running_other:
                block_reason = f"Đang ghép run `{running_other}` — đợi xong rồi ghép tiếp."
            else:
                block_reason = None

            if st.button("▶️ Bắt đầu", type="primary", disabled=bool(block_reason), help=block_reason):
                if want_capcut:
                    export_style = chosen_style
                    if use_akp and chosen_style and chosen_style.get("background_layer", {}).get("enabled"):
                        # AKP tự dựng lớp nền mờ — tắt lớp nền của bộ phong cách để khỏi chồng 2 lớp.
                        export_style = {**chosen_style, "background_layer": {"enabled": False}}
                    with st.spinner("Đang tạo project CapCut..."):
                        try:
                            draft_dir = export_capcut_draft(run_dir, canvas=canvas,
                                                            draft_name=capcut_name.strip() or None, style=export_style)
                            akp_log = []
                            if use_akp:
                                enhance_draft(draft_dir, akp_opts, log=akp_log.append)
                            # giữ qua st.rerun() bên dưới (khi ghép MP4 cùng lúc)
                            st.session_state["capcut_result"] = {"run": run_dir.name, "draft": draft_dir.name,
                                                                 "log": akp_log}
                        except Exception as e:
                            st.session_state["capcut_result"] = {"run": run_dir.name,
                                                                 "error": f"{type(e).__name__}: {e}"}
                if want_mp4:
                    start_render(
                        run_dir, canvas=canvas, font_path=font_path_input or None, fps=int(fps),
                        on_done=_log_run_to_db,
                    )
                    st.rerun()

            capcut_result = st.session_state.get("capcut_result")
            if capcut_result and capcut_result["run"] == run_dir.name:
                if capcut_result.get("error"):
                    st.error(f"[LỖI] Xuất CapCut thất bại: {capcut_result['error']}")
                else:
                    st.success(f"Đã tạo project CapCut **{capcut_result['draft']}** — mở CapCut, "
                               "project nằm đầu danh sách.")
                    if capcut_result["log"]:
                        with st.expander("Nhật ký Auto Keyframe Pro"):
                            st.code("\n".join(capcut_result["log"]), language=None)

            if is_running:
                _render_progress(run_dir)
            elif status and status.get("state") == "error":
                st.error(f"[LỖI] Ghép video thất bại: {status.get('error')}")
            elif status and status.get("state") == "interrupted":
                st.warning(
                    "Lần ghép trước bị ngắt giữa chừng (server Streamlit đã tắt/khởi động lại) — "
                    "chọn 'Video MP4' rồi bấm 'Bắt đầu' để chạy lại."
                )
            elif status and status.get("state") == "done":
                st.success(f"Ghép xong lúc {status.get('finished_at', '').replace('T', ' ')}.")

            video_path = run_dir / "final_video.mp4"
            if video_path.exists():
                st.video(str(video_path))
                st.download_button(
                    "⬇️ Tải video",
                    data=video_path.read_bytes(),
                    file_name=f"{run_dir.name}_final_video.mp4",
                    mime="video/mp4",
                )
                st.caption(
                    "⚠️ Kênh dựng auto không nên auto-publish 100% — xem lại video trước khi đăng."
                )

            st.divider()

            with st.expander("➕ Lưu phong cách mới từ 1 project CapCut đã chỉnh tay"):
                st.caption(
                    "Xuất 1 video sang CapCut → chỉnh tay đoạn đầu (chữ, badge, lớp nền, chuyển cảnh, zoom...) → "
                    "quay về màn hình chính CapCut → chọn project đó ở đây. Tool lấy mẫu từ đoạn ĐẦU TIÊN của mỗi "
                    "track; track nào bạn xoá (vd card lý do chọn) sẽ được tắt."
                )
                drafts_root = default_drafts_root()
                drafts = sorted(
                    (p for p in drafts_root.iterdir() if (p / "draft_content.json").exists()),
                    key=lambda p: p.stat().st_mtime, reverse=True,
                ) if drafts_root else []
                source = st.selectbox("Project mẫu", [p.name for p in drafts])
                new_style_name = st.text_input("Tên bộ phong cách (vd kenh-review-chuot)", "")
                new_style_desc = st.text_input("Ghi chú (không bắt buộc)", "")
                if st.button("💾 Lưu phong cách", disabled=not (source and new_style_name.strip())):
                    name = slugify(new_style_name)
                    try:
                        content = json.loads((drafts_root / source / "draft_content.json").read_text(encoding="utf-8"))
                        style = extract_style(content, name, new_style_desc, source)
                        save_style(style, name)
                        st.success(f"Đã lưu bộ phong cách **{name}**:")
                        for line in describe_style(style):
                            st.markdown(f"- {line}")
                    except Exception as e:
                        st.error(f"[LỖI] Không học được phong cách: {type(e).__name__}: {e}")


# --------------------------------------------------------- tab: history --

with tab_history:
    st.subheader("Lịch sử các lần chạy + hiệu suất")
    runs = list_runs(db_path=DB_PATH)
    if not runs:
        st.info("Chưa có video nào được ghép xong (tự động log sau khi ghép video ở tab 'Video').")
    else:
        st.dataframe(
            [
                {
                    "Run": r["run_id"],
                    "Ngày": (r["created_at"] or "")[:10],
                    "Ngách": r["niche"],
                    "Tiêu đề": r["video_title"],
                    "View": r["views"],
                    "CTR %": r["ctr"],
                    "Click affiliate": r["affiliate_clicks"],
                    "Hoa hồng (VNĐ)": r["revenue"],
                    "YouTube": r["youtube_url"],
                }
                for r in runs
            ],
            width="stretch",
        )

        with_perf = [r for r in runs if r["views"] is not None]
        if with_perf:
            best = max(with_perf, key=lambda r: r["views"] or 0)
            st.success(f"🏆 View cao nhất: `{best['run_id']}` ({best['niche']}) — {best['views']} view, tiêu đề: {best['video_title']}")

        st.divider()
        st.write("**Nhập số liệu hiệu suất** (tự theo dõi trên YouTube Studio / Shopee Affiliate — tool không có API tự động lấy)")
        run_id_choice = st.selectbox("Run", [r["run_id"] for r in runs])
        c1, c2, c3 = st.columns(3)
        views = c1.number_input("Lượt xem", min_value=0, value=0, step=1)
        ctr = c2.number_input("CTR thumbnail (%)", min_value=0.0, value=0.0, step=0.1)
        clicks = c3.number_input("Click affiliate", min_value=0, value=0, step=1)
        c4, c5 = st.columns(2)
        revenue = c4.number_input("Hoa hồng thực tế (VNĐ)", min_value=0.0, value=0.0, step=1000.0)
        youtube_url = c5.text_input("Link YouTube đã đăng", "")

        if st.button("💾 Lưu hiệu suất"):
            ok = update_performance(
                run_id_choice,
                db_path=DB_PATH,
                views=views or None,
                ctr=ctr or None,
                affiliate_clicks=clicks or None,
                revenue=revenue or None,
                youtube_url=youtube_url or None,
            )
            if ok:
                st.success("Đã cập nhật.")
                st.rerun()
            else:
                st.error(f"Không tìm thấy run_id '{run_id_choice}'.")
