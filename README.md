# Báo cáo điểm danh lớp học online

Trang web tạo báo cáo điểm danh (sĩ số, vắng có phép/không phép, tương tác tốt/kém) và bảng xếp hạng tin nhắn từ file Zoom. Chạy hoàn toàn trong trình duyệt, không có máy chủ.

## Quyền riêng tư
Kho này **không chứa dữ liệu học sinh nào**. Danh sách lớp và file Zoom do người dùng tải lên mỗi lần dùng và chỉ được xử lý trong trình duyệt. Phần "Cài đặt lớp" (đã nghỉ hẳn, học thử...) lưu trong trình duyệt của bạn, không lên GitHub.
**Đừng commit file danh sách lớp, file chat hay file người tham gia vào kho này.**

## Đưa lên GitHub
1. Tạo kho mới (công khai được), bấm **Add file → Upload files**.
2. Kéo thả 4 file: `index.html`, `tao_bao_cao.py`, `cau_noi_web.py`, `.nojekyll` rồi **Commit changes**. (`.nojekyll` là file ẩn; nếu khó chọn, bỏ qua cũng được.)
3. Vào **Settings → Pages**, mục *Build and deployment* chọn **Deploy from a branch**, nhánh `main`, thư mục `/ (root)`, bấm Save.
4. Sau 1–2 phút, trang có địa chỉ `https://<tên-tài-khoản>.github.io/<tên-kho>/`.

Mở file `index.html` trực tiếp từ máy sẽ không chạy được (trình duyệt chặn đọc file `.py`); hãy dùng địa chỉ GitHub Pages.

## Cách dùng
1. Nhập tên lớp, tải danh sách lớp (.csv).
2. Tải file chat Zoom (.txt, được chọn nhiều file) và file người tham gia (.csv).
3. Chọn học sinh xin nghỉ trong ô tìm kiếm.
4. (Một lần) điền cài đặt lớp, rồi bấm **Tạo báo cáo** và sao chép.

Các dòng có ❓ hoặc ⚠️ trong báo cáo là ca cần bạn tự kiểm tra trước khi gửi nhóm lớp.

## Khác với bản dùng trong Claude
- Không đọc ảnh chụp Zalo: chọn tên người xin nghỉ trong danh sách.
- Không có trợ lý hỏi lại: ca mơ hồ hiện thành cảnh báo để bạn tự quyết.
- Tự nhận file danh sách dùng dấu `;` (Excel tiếng Việt) hoặc `,`.
- Tên giảng viên/trợ giảng nhập ở phần Cài đặt lớp (mặc định chỉ nhận dấu hiệu chung "GV", "TG").
- Bỏ thư viện `unidecode`, dùng hàm bỏ dấu riêng (khác biệt chỉ với ký tự ngoài chữ Latin).
