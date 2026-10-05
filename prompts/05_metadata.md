Năm hiện tại: {{CURRENT_YEAR}} (chỉ dùng năm này nếu cần nhắc năm trong tiêu đề/thumbnail, không
tự đoán năm khác).

Ngách (JSON):
{{NICHE_JSON}}

5 sản phẩm đã chọn (JSON, có "rank" 1-5, tên gốc trên Shopee thường dài/nhiều từ khóa SEO):
{{SELECTED_PRODUCTS_JSON}}

Trích đoạn hook mở đầu kịch bản:
{{SCRIPT_HOOK_EXCERPT}}

Nhiệm vụ: Sinh metadata tối ưu SEO + phần mở đầu cho mô tả video YouTube review Top 5 affiliate,
giọng gần gũi tự nhiên như đang nói chuyện với người xem, có thể dùng emoji hợp lý (không lạm dụng).

Chỉ trả về JSON đúng theo format sau, không thêm giải thích ngoài JSON:
{
  "title": "tiêu đề video, dưới 100 ký tự, có yếu tố giật tít hợp lý nhưng không sai sự thật",
  "hook_description": "đoạn mở đầu mô tả video, 3-5 câu, giọng gần gũi tự nhiên, có 1-2 emoji hợp lý, kết bằng 1 câu mời người xem để lại bình luận sản phẩm họ thích nhất (kiểu '...để lại bình luận cho mình biết với nhé! 👇')",
  "hook_label": "nhãn cực ngắn (dưới 8 từ) cho mốc 0:00 trong danh sách timestamp, tóm tắt câu hỏi/mở đầu hấp dẫn của video",
  "outro_label": "nhãn cực ngắn (dưới 8 từ) cho mốc tổng kết cuối video, kiểu 'Tổng kết: ...'",
  "tags": ["tag1", "tag2"],
  "thumbnail_text": "một câu ngắn gợi ý chữ đặt trên thumbnail"
}
