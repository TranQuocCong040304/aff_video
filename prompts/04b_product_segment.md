Ngách (JSON):
{{NICHE_JSON}}

Sản phẩm cần viết lời bình (JSON):
{{PRODUCT_JSON}}

Bảng xếp hạng đầy đủ của video — bậc thang giá từ BÌNH DÂN (Top 5) lên CAO CẤP (Top 1), video
đếm ngược Top 5 -> Top 1, hạng càng cao càng xịn và càng đắt:
{{RANKING_JSON}}

Vị trí trong video: hạng {{RANK}}/5.

Lời bình phải KHỚP với thứ hạng/phân khúc này:
- Hạng 5-2: nói rõ phân khúc (xem "tier"), khen điểm mạnh thật và điểm hơn hẳn hạng ngay dưới
  (nếu có), rồi nói thật lòng điểm còn thiếu (xem "weakness"/"needs_missing") — lý do nó chưa lên
  được hạng cao hơn, gợi ý ai nên mua nó (ví dụ: ngân sách eo hẹp, nhu cầu cơ bản...). KHÔNG dùng
  các cụm như "tốt nhất", "đáng mua nhất", "quán quân", "không đối thủ" cho các hạng này.
- Hạng 1: lựa chọn cao cấp nhất — nhấn mạnh nó bù được HẾT những điểm mà các hạng dưới còn thiếu
  (so sánh ngắn với 1-2 sản phẩm hạng dưới trong bảng trên), đáp ứng trọn vẹn mọi nhu cầu.

CHÊ NHẸ (bắt buộc với MỌI hạng, kể cả Top 1): đúng 1 lần trong cả đoạn, chê 1 lỗi nhỏ lặt vặt —
ưu tiên điều NHÌN THẤY được trong ảnh/video (vỏ bóng dễ bám vân tay, nút bấm hơi nhỏ, dây hơi
ngắn, màu hơi kén nội thất...), nếu không thấy gì thì dùng "nitpick" trong JSON. Cách chê:
- Nhẹ nhàng, thật lòng như người dùng thật, kiểu "điểm mình hơi tiếc là...", "chê một chút là...".
- Ngay sau đó trấn an rằng lỗi này nhỏ, bỏ qua được / dễ khắc phục, rồi quay lại điểm mạnh.
- TUYỆT ĐỐI không chê nặng (hỏng, kém an toàn, không đáng tiền...), không để người xem mất hứng mua.
- Lời chê nhẹ này KHÁC với "điểm còn thiếu so với hạng trên" ở hạng 5-2 — hai ý tách bạch.

Nhiệm vụ: Viết đoạn lời bình (voice-over) giới thiệu RIÊNG sản phẩm này.

{{IMAGE_INSTRUCTION}}

Yêu cầu áp dụng cho MỌI câu:
- Giọng văn tự nhiên, gần gũi như người thật đang chia sẻ trải nghiệm cá nhân, KHÔNG liệt kê
  thông số khô khan, KHÔNG viết như quảng cáo cứng.
- Có thể nhắc tới giá và lý do sản phẩm này đứng ở vị trí hạng {{RANK}} (không nhất thiết
  phải nhắc ở mọi khối ảnh, chỉ cần nhắc ít nhất 1 lần trong cả đoạn).
- Chèn thẻ cảm xúc ngay trong câu khi phù hợp: [cười nhẹ], [thở dài], [nhấn mạnh], [ngập ngừng],
  [hào hứng] — đây là tín hiệu để hệ thống đọc giọng có cảm xúc thật (đổi tông/tốc độ/thêm khoảng
  lặng), không phải chỉ để trang trí, nên đừng bỏ qua. Câu chê nhẹ hợp với [ngập ngừng] hoặc
  [cười nhẹ].

Chỉ trả về đúng nội dung theo đúng định dạng yêu cầu ở trên, không thêm giải thích, không thêm
tiêu đề, không nhắc lại tên sản phẩm như một dòng riêng.
