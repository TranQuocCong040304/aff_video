"""CLI: chọn 5 sản phẩm -> (bạn lấy ảnh thật) -> script bám ảnh -> audio -> video.

Luồng mặc định dừng lại sau khi chọn 5 sản phẩm để bạn vào từng link Shopee lấy
ảnh/video chi tiết thật, vì script ở Bước 2 sẽ viết bám theo đúng ảnh đó.

Ví dụ:
    python main.py
    # -> xem data/output/<run_id>/03b_product_links.md, vào từng link tải ảnh vào
    #    data/output/<run_id>/product_media/<rank>_<ten-sp>/, rồi:
    python main.py --script-only data/output/<run_id>

    # Muốn bỏ qua bước dừng (test nhanh, không cần ảnh thật):
    python main.py --auto-script

    # Sửa tay script rồi chỉ chạy lại audio (không tốn API Gemini):
    python main.py --media-only data/output/<run_id>

    # Chỉ ghép lại video từ audio/ảnh đã có sẵn:
    python main.py --assemble-only data/output/<run_id> --vertical

    # Xuất thành project CapCut để tự biên tập (thay cho ghép MP4):
    python main.py --capcut data/output/<run_id> --style kenh-1
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from src.capcut_export import capcut_is_running, export_capcut_draft
from src.capcut_style import list_styles, load_style
from src.database import list_runs, log_run, update_performance
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
from src.product_links import create_media_folders, write_product_links_file
from src.tts import DEFAULT_VOICE
from src.video_assembly import assemble_video

# Console Windows mặc định không phải UTF-8 -> in tiếng Việt có dấu sẽ crash
# (UnicodeEncodeError). Ép stdout/stderr sang UTF-8 để chạy được trên mọi máy.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--voice", default=None, help=f"Giọng edge-tts (mặc định {DEFAULT_VOICE})")
    parser.add_argument("--rate", default="+0%", help="Tốc độ đọc edge-tts, vd +10%%, -5%%")
    parser.add_argument("--vertical", action="store_true", help="Xuất video dọc 1080x1920 (Shorts) thay vì ngang 1920x1080")
    parser.add_argument("--fps", type=int, default=30, help="FPS video xuất ra (mặc định 30)")
    parser.add_argument("--font", default=None, help="Đường dẫn font .ttf tùy chỉnh cho phụ đề/badge")
    parser.add_argument("--skip-media", action="store_true", help="Chỉ sinh script/metadata, bỏ qua TTS + ảnh + video")
    parser.add_argument("--skip-assembly", action="store_true", help="Dừng sau bước audio + ảnh, chưa ghép video")


def _canvas(args) -> tuple:
    return (1080, 1920) if args.vertical else (1920, 1080)


def _fmt(value, suffix: str = "") -> str:
    return f"{value}{suffix}" if value is not None else "-"


def _print_report() -> int:
    runs = list_runs()
    if not runs:
        print("Chưa có lần chạy nào được ghi lại. Ghép xong 1 video thì tự động log vào data/history.db.")
        return 0

    print(f"{'Run':<17} {'Ngày':<11} {'Ngách':<28} {'View':>8} {'CTR':>6} {'Click':>6} {'Hoa hồng':>12} {'Đã đăng':<8}")
    print("-" * 100)
    for r in runs:
        date = (r["created_at"] or "")[:10]
        niche = (r["niche"] or "?")[:26]
        published = "co" if r["youtube_url"] else "-"
        print(
            f"{r['run_id']:<17} {date:<11} {niche:<28} "
            f"{_fmt(r['views']):>8} {_fmt(r['ctr'], '%'):>6} {_fmt(r['affiliate_clicks']):>6} "
            f"{_fmt(int(r['revenue']) if r['revenue'] is not None else None):>12} {published:<8}"
        )

    with_perf = [r for r in runs if r["views"] is not None]
    if with_perf:
        best = max(with_perf, key=lambda r: r["views"] or 0)
        print(f"\nView cao nhất: {best['run_id']} ({best['niche']}) — {best['views']} view, tiêu đề: {best['video_title']}")
    else:
        print("\nChưa có run nào được nhập số liệu hiệu suất — dùng --log-performance sau khi đăng video.")
    return 0


def _log_run_to_db(run_dir: Path, video_path: Path) -> None:
    """Ghi lịch sử vào data/history.db sau khi ghép video xong — đọc lại từ
    các file JSON đã lưu nên gọi được ở mọi luồng (mặc định, --media-only,
    --assemble-only) mà không cần truyền dữ liệu qua nhiều lớp hàm."""
    niche = None
    niche_path = run_dir / "01_niche.json"
    if niche_path.exists():
        niche = json.loads(niche_path.read_text(encoding="utf-8")).get("niche")

    title = None
    metadata_path = run_dir / "05_metadata.json"
    if metadata_path.exists():
        title = json.loads(metadata_path.read_text(encoding="utf-8")).get("title")

    product_names = []
    products_path = run_dir / "03_products_selected.json"
    if products_path.exists():
        data = json.loads(products_path.read_text(encoding="utf-8"))
        product_names = [p["name"] for p in data.get("selected_products", [])]

    log_run(run_dir.name, niche, title, str(video_path), product_names)


def _init_clients():
    """vision_client (Gemini, xem ảnh/video) + text_client (Groq, mọi bước còn lại).
    Trả về (vision_client, text_client, error_message_or_None)."""
    try:
        vision_client = GeminiClient()
    except RuntimeError as e:
        return None, None, f"[LỖI CẤU HÌNH - GEMINI] {e}"
    try:
        text_client = GroqClient()
    except RuntimeError as e:
        return None, None, f"[LỖI CẤU HÌNH - GROQ] {e}"
    return vision_client, text_client, None


def _init_text_client_soft():
    """Chỉ cần Groq (dùng cho bước sinh chữ nhấn mạnh trong media_step) — lỗi
    thì trả None thay vì chặn cả lệnh, vì đây chỉ là phần trang trí thêm."""
    try:
        return GroqClient()
    except RuntimeError as e:
        print(f"[CẢNH BÁO] {e} — bỏ qua phần chữ nhấn mạnh (highlight), audio/video vẫn ghép bình thường.")
        return None


def _init_gemini_client_soft():
    """Chỉ cần Gemini (dùng cho Gemini TTS ở hook/CTA trong media_step) — lỗi
    thì trả None thay vì chặn cả lệnh, hook/CTA sẽ tự dùng edge-tts như cũ."""
    try:
        return GeminiClient()
    except RuntimeError as e:
        print(f"[CẢNH BÁO] {e} — bỏ qua Gemini TTS cho hook/CTA, dùng edge-tts như các đoạn khác.")
        return None


def _continue_after_script(run_dir: Path, script: str, selected_products: dict, args, voice: str,
                            text_client=None, gemini_client=None) -> int:
    if args.skip_media:
        print(
            f"\n>>> Đã bỏ qua bước audio (--skip-media). Sửa {run_dir / '04_script.txt'} nếu cần, "
            f"rồi chạy: python main.py --media-only {run_dir}"
        )
        return 0

    print("\nĐang sinh audio (edge-tts) + tải ảnh sản phẩm (thumbnail để ghép video)...")
    run_media_step(script, selected_products, run_dir, voice=voice, rate=args.rate,
                    text_client=text_client, gemini_client=gemini_client)
    print(f"Xong bước audio/ảnh. Xem {run_dir / '06_media_manifest.json'} và {run_dir / '08_youtube_description.txt'}.")

    if args.skip_assembly:
        print(f"\n>>> Đã bỏ qua ghép video (--skip-assembly). Chạy: python main.py --assemble-only {run_dir}")
        return 0

    print("Đang ghép video (có thể mất vài phút)...")
    out_path = assemble_video(run_dir, canvas=_canvas(args), font_path=args.font, fps=args.fps)
    _log_run_to_db(run_dir, out_path)
    print(f"\nXong. Video tại: {out_path}")
    print(">>> Bước tiếp theo: XEM LẠI video trước khi đăng — kênh dựng auto không nên auto-publish 100%.")
    print(f">>> Sau khi đăng, theo dõi vài ngày rồi nhập số liệu: python main.py --log-performance {run_dir} --views N --ctr X --clicks Y --revenue Z --youtube-url <link>")
    return 0


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Sinh video review affiliate từ CSV/JSON, bám theo ảnh sản phẩm thật.")
    parser.add_argument(
        "--keywords", default="data/input",
        help="File CSV từ khóa, hoặc thư mục chứa nhiều file CSV (gộp + dedupe tự động)",
    )
    parser.add_argument(
        "--products", default=None,
        help="File JSON sản phẩm Shopee. Bỏ trống để tự tìm file .json duy nhất trong data/input/",
    )
    parser.add_argument("--out", default="data/output", help="Thư mục gốc để lưu kết quả")
    parser.add_argument(
        "--auto-script", action="store_true",
        help="Bỏ qua bước dừng lấy ảnh thật — viết script ngay chỉ dựa vào tên/giá (test nhanh).",
    )
    parser.add_argument(
        "--script-only", metavar="RUN_DIR", default=None,
        help="Viết script (Bước 2) từ 01_niche.json + 03_products_selected.json đã có sẵn trong "
             "RUN_DIR, dùng ảnh thật trong RUN_DIR/product_media/ nếu có — không gọi lại Prompt 1-3.",
    )
    parser.add_argument(
        "--media-only", metavar="RUN_DIR", default=None,
        help="Chỉ chạy lại TTS + tải ảnh (+ ghép video) trên 1 thư mục output đã có sẵn "
             "(vd sau khi sửa tay 04_script.txt) — không gọi lại Gemini.",
    )
    parser.add_argument(
        "--assemble-only", metavar="RUN_DIR", default=None,
        help="Chỉ ghép lại video MP4 từ 06_media_manifest.json đã có sẵn — không gọi Gemini, không chạy lại TTS.",
    )
    parser.add_argument(
        "--capcut", metavar="RUN_DIR", default=None,
        help="Xuất RUN_DIR thành project CapCut (thay vì ghép MP4) để biên tập tiếp trong CapCut desktop.",
    )
    parser.add_argument(
        "--style", default=None,
        help=f"Bộ phong cách CapCut (file styles/<tên>.json, dùng với --capcut). Hiện có: {', '.join(list_styles()) or 'chưa có'}",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="In bảng lịch sử + hiệu suất tất cả các lần chạy (data/history.db) rồi thoát.",
    )
    parser.add_argument(
        "--log-performance", metavar="RUN_DIR", default=None,
        help="Nhập số liệu hiệu suất sau khi đăng video (tự theo dõi trên YouTube/Shopee Affiliate, "
             "tool không có API tự động lấy) — dùng kèm --views/--ctr/--watch-time/--clicks/--revenue/--youtube-url.",
    )
    parser.add_argument("--views", type=int, default=None, help="Số lượt xem (dùng với --log-performance)")
    parser.add_argument("--ctr", type=float, default=None, help="CTR thumbnail %% (dùng với --log-performance)")
    parser.add_argument("--watch-time", type=int, default=None, help="Tổng watch time (giây, dùng với --log-performance)")
    parser.add_argument("--clicks", type=int, default=None, help="Số click qua link affiliate (dùng với --log-performance)")
    parser.add_argument("--revenue", type=float, default=None, help="Hoa hồng thực tế VNĐ (dùng với --log-performance)")
    parser.add_argument("--youtube-url", default=None, help="Link video đã đăng (dùng với --log-performance)")
    _add_common_args(parser)
    args = parser.parse_args()

    voice = args.voice or os.environ.get("EDGE_TTS_VOICE", DEFAULT_VOICE)

    if args.report:
        return _print_report()

    if args.log_performance:
        run_id = Path(args.log_performance).name
        ok = update_performance(
            run_id, views=args.views, ctr=args.ctr, watch_time_seconds=args.watch_time,
            affiliate_clicks=args.clicks, revenue=args.revenue, youtube_url=args.youtube_url,
        )
        if not ok:
            print(f"[LỖI] Không tìm thấy run_id '{run_id}' trong data/history.db — video này đã ghép xong chưa?", file=sys.stderr)
            return 1
        print(f"Đã cập nhật hiệu suất cho {run_id}. Xem toàn bộ: python main.py --report")
        return 0

    if args.capcut:
        run_dir = Path(args.capcut)
        print(f"Đang tạo project CapCut từ {run_dir}...")
        if capcut_is_running():
            print("[CẢNH BÁO] CapCut đang mở — nên đóng CapCut trước để project mới hiện trong danh sách.")
        try:
            style = load_style(args.style) if args.style else None
            draft_dir = export_capcut_draft(run_dir, canvas=_canvas(args), style=style)
        except (FileNotFoundError, ValueError) as e:
            print(f"[LỖI] {e}", file=sys.stderr)
            return 1
        print(f"\nXong. Mở CapCut, project '{draft_dir.name}' nằm đầu danh sách.")
        return 0

    if args.assemble_only:
        run_dir = Path(args.assemble_only)
        print(f"Đang ghép video từ {run_dir} (chế độ --assemble-only)...")
        try:
            out_path = assemble_video(run_dir, canvas=_canvas(args), font_path=args.font, fps=args.fps)
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            print(f"[LỖI] {e}", file=sys.stderr)
            return 1
        _log_run_to_db(run_dir, out_path)
        print(f"\nXong. Video tại: {out_path}")
        return 0

    if args.media_only:
        run_dir = Path(args.media_only)
        script_path = run_dir / "04_script.txt"
        products_path_json = run_dir / "03_products_selected.json"
        if not script_path.exists() or not products_path_json.exists():
            print(f"[LỖI] Thiếu 04_script.txt hoặc 03_products_selected.json trong {run_dir}", file=sys.stderr)
            return 1
        script = script_path.read_text(encoding="utf-8")
        selected_products = json.loads(products_path_json.read_text(encoding="utf-8"))
        print(f"Đang sinh audio + tải ảnh từ {run_dir} (chế độ --media-only)...")
        return _continue_after_script(run_dir, script, selected_products, args, voice,
                                       text_client=_init_text_client_soft())

    if args.script_only:
        run_dir = Path(args.script_only)
        niche_path = run_dir / "01_niche.json"
        products_path_json = run_dir / "03_products_selected.json"
        if not niche_path.exists() or not products_path_json.exists():
            print(f"[LỖI] Thiếu 01_niche.json hoặc 03_products_selected.json trong {run_dir}", file=sys.stderr)
            return 1
        niche = json.loads(niche_path.read_text(encoding="utf-8"))
        selected_products = json.loads(products_path_json.read_text(encoding="utf-8"))

        vision_client, text_client, err = _init_clients()
        if err:
            print(err, file=sys.stderr)
            return 1

        print(f"Đang viết script từ {run_dir} (dùng ảnh thật trong product_media/ nếu có)...")
        try:
            script_result = run_script_stage(niche, selected_products, vision_client, text_client, media_dir=run_dir)
        except (LLMJSONError, LLMQuotaExceededError, LLMServerError) as e:
            print(f"[LỖI] {e}", file=sys.stderr)
            if (run_dir / "04_script_progress.json").exists():
                print(
                    f"Tiến độ đã hoàn thành (hook/từng sản phẩm/CTA đã sinh) được lưu tại "
                    f"{run_dir / '04_script_progress.json'} — chạy lại đúng lệnh này sau, "
                    f"tool sẽ bỏ qua phần đã xong, không tốn quota làm lại.",
                    file=sys.stderr,
                )
            return 1
        save_script_result(script_result, run_dir)
        print(f"Đã lưu {run_dir / '04_script.txt'} và {run_dir / '05_metadata.json'}.")
        return _continue_after_script(run_dir, script_result.script, selected_products, args, voice,
                                       text_client=text_client)

    # --- Luồng mặc định: chạy từ đầu (Prompt 1-3) ---
    products_path = args.products
    if products_path is None:
        candidates = sorted(Path("data/input").glob("*.json"))
        if len(candidates) == 1:
            products_path = candidates[0]
        elif len(candidates) == 0:
            print("[LỖI DỮ LIỆU ĐẦU VÀO] Không tìm thấy file .json nào trong data/input/.", file=sys.stderr)
            return 1
        else:
            names = ", ".join(p.name for p in candidates)
            print(
                f"[LỖI DỮ LIỆU ĐẦU VÀO] Có {len(candidates)} file .json trong data/input/ ({names}). "
                f"Chỉ định rõ bằng --products <đường dẫn>.",
                file=sys.stderr,
            )
            return 1

    try:
        keywords = load_keywords(args.keywords)
        products = load_products(products_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"[LỖI DỮ LIỆU ĐẦU VÀO] {e}", file=sys.stderr)
        return 1

    print(f"Đã đọc {len(keywords)} từ khóa và {len(products)} sản phẩm.")

    vision_client, text_client, err = _init_clients()
    if err:
        print(err, file=sys.stderr)
        return 1

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) / run_id

    print("Đang chọn ngách + lọc 5 sản phẩm (Prompt 1-3, có thể mất khoảng 1 phút)...")
    try:
        selection = run_selection_stage(keywords, products, text_client)
    except (LLMJSONError, LLMQuotaExceededError, LLMServerError) as e:
        print(f"[LỖI] {e}", file=sys.stderr)
        return 1

    save_selection_result(selection, out_dir)
    links_path = write_product_links_file(selection.selected_products, out_dir)
    create_media_folders(selection.selected_products, out_dir)

    print(f"\nĐã chọn xong 5 sản phẩm. Kết quả tại: {out_dir}")
    print(f"  - Ngách: {selection.niche.get('niche')}")

    if not args.auto_script:
        print(
            f"\n>>> Xem {links_path} — vào từng link Shopee tải ảnh chi tiết/chụp màn hình video vào\n"
            f">>> thư mục product_media/<rank>_<ten-sp>/ tương ứng (có thể để trống nếu không có ảnh).\n"
            f">>> Xong thì chạy: python main.py --script-only {out_dir}"
        )
        return 0

    print("(--auto-script) Bỏ qua bước lấy ảnh thật, viết script ngay...")
    try:
        script_result = run_script_stage(
            selection.niche, selection.selected_products, vision_client, text_client, media_dir=None
        )
    except (LLMJSONError, LLMQuotaExceededError, LLMServerError) as e:
        print(f"[LỖI] {e}", file=sys.stderr)
        return 1
    save_script_result(script_result, out_dir)
    print(f"  - Tiêu đề gợi ý: {script_result.metadata.get('title')}")
    return _continue_after_script(out_dir, script_result.script, selection.selected_products, args, voice,
                                   text_client=text_client)


if __name__ == "__main__":
    raise SystemExit(main())
