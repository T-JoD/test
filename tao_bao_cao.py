#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tao_bao_cao.py — bộ máy tạo báo cáo điểm danh + bảng xếp hạng tin nhắn Zoom
(phiên bản WEB: chạy trong trình duyệt qua Pyodide, không cần cài gì).

Khác với bản dùng trong Claude:
- KHÔNG chứa dữ liệu học sinh nào. Danh sách lớp (CSV) và các ghi chú cố định
  (đã nghỉ hẳn, học thử, vắng cố định theo thứ) do người dùng tải lên / nhập
  trên trang web mỗi lần dùng, nên có thể để mã nguồn này ở kho công khai.
- generate_report() nhận thêm `roster_csv`, `fixed_notes`, `roster_id_regex`.
- Không còn `unidecode`: dùng hàm bỏ dấu riêng (chỉ dùng thư viện chuẩn).

Mọi luật nghiệp vụ (ghép tên, điểm danh, tương tác tốt/kém, xin nghỉ...) giữ
nguyên như bản gốc. Xem README.md để biết cách dùng.
"""
import re, csv, os, io, unicodedata
from collections import defaultdict, Counter
from datetime import datetime

# Dấu hiệu chung nhận ra giảng viên/trợ giảng qua tên hiển thị. Tên cụ thể của
# giảng viên do người dùng nhập trên trang web (extra_teacher_names).
TEACHER_SUBSTR_NORM = ["tg", "gv"]
VN_WEEKDAY = {0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm",
              4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ Nhật"}

FULL_MSG_LIMIT = 20
TRUNCATE_SHOW = 6

DEFAULT_GOOD_THRESHOLD = 10
DEFAULT_POOR_THRESHOLD = 4

# Bảng chuyển các chữ Latin đặc biệt không tách được thành "chữ + dấu".
_SPECIAL_LATIN = {"đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "ł": "l", "Ł": "L",
                  "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
                  "ß": "ss", "þ": "th", "Þ": "TH", "ı": "i"}

def unidecode(s: str) -> str:
    """Bỏ dấu tiếng Việt (và dấu Latin khác) → chữ ASCII. Thay cho thư viện
    `unidecode` để chạy được trong trình duyệt mà không cần tải thêm gói.
    Ký tự ngoài chữ Latin (emoji, chữ Hán...) bị bỏ qua."""
    s = "".join(_SPECIAL_LATIN.get(ch, ch) for ch in s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.encode("ascii", "ignore").decode("ascii")

# ======================= CHUẨN HÓA TÊN =======================
def norm(s: str) -> str:
    s = unidecode(s).lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def norm_vn(s: str) -> str:
    """Chuẩn hoá GIỮ NGUYÊN dấu tiếng Việt (khác `norm()` ở trên, vốn dùng
    unidecode bỏ hết dấu để so khớp bền với các tên KHÔNG gõ dấu). Dùng
    unicodedata.normalize("NFC", ...) TRƯỚC khi hạ chữ thường — bắt buộc, vì
    cùng 1 chữ có dấu có thể được mã hoá theo 2 dạng Unicode khác nhau (dựng
    sẵn/NFC hay tổ hợp dấu rời/NFD) tuỳ ứng dụng gõ (Zoom trên máy khác nhau,
    Zalo...); nếu không ép về cùng 1 dạng, 2 chuỗi NHÌN GIỐNG HỆT NHAU vẫn so
    sánh ra khác nhau — một bug mới còn khó phát hiện hơn cả việc bỏ dấu.
    CHỈ dùng để PHÁ THẾ HOÀ (xem resolve_ties_by_diacritics), không dùng để
    chấm điểm chính — không thể thay thế norm() vì rất nhiều học sinh không
    gõ dấu khi tự điền tên/tự giới thiệu."""
    s = unicodedata.normalize("NFC", s).lower()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()

def has_diacritics(s: str) -> bool:
    """True nếu chuỗi có ít nhất 1 chữ tiếng Việt có dấu — so sánh dạng giữ
    dấu (norm_vn) với dạng đã bỏ dấu (norm); khác nhau nghĩa là có dấu thật.
    Dùng làm ĐIỀU KIỆN AN TOÀN bắt buộc trước khi dùng dấu để phá thế hoà:
    nếu người gõ hoàn toàn không gõ dấu thì không có bằng chứng gì để dùng,
    phải giữ nguyên hành vi ambiguous cũ."""
    return norm_vn(s) != norm(s)

def _submultiset_either_way(a: Counter, b: Counter) -> bool:
    """True nếu a là tập con đa tập (sub-multiset) của b, HOẶC ngược lại —
    mỗi từ trong Counter "nhỏ hơn" phải xuất hiện ĐỦ số lần trong Counter kia
    (không chỉ có mặt). Dùng chung ở score_all() (bonus subset khi khớp
    sender Zoom) và fuzzy_candidates_for_excused_name() (tiêu chí duy nhất
    khi khớp tên xin nghỉ — xem docstring hàm đó để biết vì sao KHÔNG cộng
    thêm bonus liền mạch/thứ tự ở ngữ cảnh đó, dù score_all() có dùng)."""
    return not (a - b) or not (b - a)

def strip_stt(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"^\(?\s*\d{1,3}\s*[\.\-–]?\s*", "", s)
    s = re.sub(r"\(.*?\)", "", s).strip()
    return s

def extract_leading_stt(raw: str):
    m = re.match(r"^\(?\s*(\d{1,3})\s*[\.\-–]?\s*", raw.strip())
    return int(m.group(1)) if m else None

def is_teacher(name: str, extra_substr_norm=None) -> bool:
    """So khớp tên GV/TG bằng RANH GIỚI TỪ (dùng chung _contains_phrase bên dưới)
    — KHÔNG dùng substring thường `t in n`. Bản substring thường từng khiến 1
    extra_teacher_names ngắn/phổ biến (vd "An", "Anh", "Hà", "Minh"...) vô tình
    khớp trúng vào GIỮA hàng loạt tên học sinh khác sau khi bỏ dấu (vd "An"
    khớp cả vào "ngoAN", "toAN", "hoANg ANh"...), khiến các học sinh đó bị coi
    là chính GV/TG và biến mất khỏi toàn bộ báo cáo mà không có cảnh báo gì.
    _contains_phrase yêu cầu khớp là 1 TỪ/CỤM TỪ riêng biệt (có khoảng trắng
    hoặc đầu/cuối chuỗi ở 2 đầu), không tính là khớp nếu chỉ là đoạn ký tự con
    nằm giữa 1 từ khác. Lưu ý: nếu GV/TG buổi đó có tên trùng ĐÚNG 1 từ với 1
    học sinh (vd cả 2 đều tên "An" là 1 từ độc lập), 2 tên vẫn không phân biệt
    được — đây là giới hạn không thể tránh của việc chỉ so khớp theo tên, cần
    ưu tiên nhập extra_teacher_names là HỌ TÊN ĐẦY ĐỦ thay vì chỉ 1 từ khi có
    thể, để giảm rủi ro này."""
    n = norm(name)
    substrs = TEACHER_SUBSTR_NORM + [norm(s) for s in (extra_substr_norm or [])]
    return any(_contains_phrase(t, n) for t in substrs)

def find_duplicate_names(roster):
    """Trả về set các tên xuất hiện >1 lần trong roster (kể cả không nằm trong
    duplicate_name_hint) — dùng để tự động cảnh giác, không phụ thuộc việc
    người dùng có khai báo thủ công trong cấu hình lớp hay không."""
    cnt = defaultdict(int)
    for r in roster:
        cnt[r["name"]] += 1
    return {name for name, c in cnt.items() if c > 1}

def find_self_collision_names(roster):
    """Quét TRƯỚC (khi có roster mới, không phải lúc chạy chấm điểm) các
    entry mà CHÍNH TÊN CỦA HỌ có ≥2 từ trùng nhau sau khi bỏ dấu — vd
    "Trần Trân" (họ và tên đều fold về "nguyen") hay "Hồ Trần Bảo Trân" (2 trong 4 từ cùng fold về "nguyen"). Nhóm này khác hẳn
    find_duplicate_names() ở trên (hàm đó chỉ bắt 2 HỌC SINH có tên trùng Y
    HỆT nhau, vd 2 bạn cùng tên "Lê Văn Bình") — đây là bắt 1 CÁ NHÂN có
    tên riêng dễ vô tình trở thành "tập con suy biến" của rất nhiều sender
    khác trong score_all(), dù bug toán học (set thay vì Counter) đã được vá
    (xem score_all) thì nhóm tên này vẫn đáng chú ý riêng khi rà soát báo cáo
    thủ công, vì luôn là điểm dễ xảy ra hoà điểm/nhầm lẫn nhất trong roster.

    Trả về list các tên (kèm STT nếu có) thuộc nhóm này — nên chạy 1 lần mỗi
    khi roster có cập nhật (thêm/đổi tên học sinh), KHÔNG cần chạy mỗi buổi."""
    flagged = []
    for r in roster:
        toks = norm(r["name"]).split()
        if len(toks) != len(set(toks)):
            flagged.append(r)
    return flagged

def entry_key(entry):
    """Khóa định danh DUY NHẤT cho từng học sinh trong roster, kể cả khi 2
    học sinh trùng tên (dùng id() của object dict, vì mỗi entry trong roster
    là 1 object riêng biệt được tạo 1 lần trong load_roster)."""
    return id(entry)

def stt_variants(entry):
    """Tập các số thứ tự có thể dùng để nhận diện 1 học sinh: (a) `stt` = số
    trong CỘT STT của roster, (b) `login_num` = số nằm trong "Tên đăng nhập"
    (vd r73065 -> 65). 2 số này có thể KHÁC nhau (roster R73 bị lệch 1 đơn vị
    ở vùng STT 27-79), và học sinh khi tự điểm danh có bạn gõ số này, có bạn
    gõ số kia — nên khi so số học sinh tự gõ với roster phải chấp nhận CẢ 2."""
    return {v for v in (entry.get("stt"), entry.get("login_num")) if v is not None}

def stt_matches(entry, n):
    return n in stt_variants(entry)

def pick_by_stt(entries, n):
    """Chọn đúng 1 entry trong `entries` (thường là các entry cùng tên) khớp số
    n (theo cột STT HOẶC theo số trong tên đăng nhập). Nếu >1 entry cùng khớp
    (2 số của 2 người trùng nhau chéo) thì ưu tiên khớp theo login_num; vẫn
    không phân giải được đúng 1 người thì trả None — không đoán bừa."""
    hits = [e for e in entries if stt_matches(e, n)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        hits2 = [e for e in hits if e.get("login_num") == n]
        if len(hits2) == 1:
            return hits2[0]
    return None

def display_num(entry, dup_names):
    """Số thứ tự dùng để HIỂN THỊ trong báo cáo. Với học sinh có tên bị TRÙNG
    trong roster, dùng số trong TÊN ĐĂNG NHẬP (login_num) — đúng quy ước của
    trợ giảng (số này ổn định, gắn với tài khoản); học sinh không trùng tên
    giữ nguyên số trong cột STT như cũ."""
    if entry["name"] in dup_names and entry.get("login_num") is not None:
        return entry["login_num"]
    return entry.get("stt")

def display_label(entry, dup_names):
    """Tên hiển thị: nếu tên bị trùng trong roster, LUÔN kèm số thứ tự (theo
    số trong TÊN ĐĂNG NHẬP, xem display_num) phía trước để người đọc phân biệt
    được 2 học sinh cùng tên, kể cả ở các dòng tổng hợp (vắng có phép/không
    phép, tương tác tốt/kém) chứ không chỉ ở bảng xếp hạng."""
    if entry["name"] in dup_names:
        num = display_num(entry, dup_names)
        if num is not None:
            return f"{num}. {entry['name']}"
    return entry["name"]

# ======================= ĐỌC DANH SÁCH GỐC =======================
def load_roster(path, id_regex, extra_students=None):
    """
    Đọc roster từ CSV. Hỗ trợ 2 định dạng:
    (a) 3 cột "Họ và tên,Tên đăng nhập,Ghi chú" (định dạng cũ — N42 vẫn đang
        dùng) — STT được SUY RA từ số cuối trong "Tên đăng nhập" (vd
        "a001.x@example.com" -> 1) qua `id_regex`, vì file không có cột STT
        riêng.
    (b) 4 cột "STT,Họ và tên,Tên đăng nhập,Ghi chú" (định dạng MỚI — R73 dùng
        từ bản cập nhật 15/09/2026) — STT lấy THẲNG từ cột STT chính thức,
        KHÔNG suy ra từ "Tên đăng nhập" nữa.

    QUAN TRỌNG (phát hiện 15/09/2026, làm rõ lại 19/09/2026): 2 nguồn số này
    KHÔNG LUÔN khớp nhau. "Tên đăng nhập" (vd "r73065") là mã CỐ ĐỊNH gắn với
    tài khoản; cột "STT" là vị trí hiển thị trong danh sách lớp — với R73, từ
    STT 27 đến 79 (trừ 42) cột STT đang LỚN HƠN số trong tên đăng nhập 1 đơn
    vị (Vương Đoàn Duy Khánh ở STT 26 có tên đăng nhập r73079 là dấu hiệu). Học
    sinh khi tự điểm danh phần lớn gõ số trong TÊN ĐĂNG NHẬP nhưng cũng có bạn
    gõ số trong cột STT. Vì vậy mỗi entry lưu CẢ HAI: `stt` (cột STT) và
    `login_num` (số trong tên đăng nhập); khi so số học sinh tự gõ dùng CẢ HAI
    (stt_matches/pick_by_stt), còn khi HIỂN THỊ tên bị trùng thì dùng
    `login_num` (display_num). Số học sinh gõ chỉ là bằng chứng phụ để phá thế
    hoà giữa các bạn trùng tên — bằng chứng chính vẫn là TÊN (học sinh hay gõ
    nhầm số).
    """
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    # Bản web: tự nhận dấu phân cách. File xuất từ Excel tiếng Việt thường dùng
    # dấu chấm phẩy (;) thay vì dấu phẩy — đếm trên dòng tiêu đề thật.
    delim = ","
    for ln in text.splitlines():
        low = ln.strip().lower()
        if low.startswith("stt") or "họ và tên" in low:
            delim = ";" if ln.count(";") > ln.count(",") else ","
            break
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    roster = []
    pat = re.compile(id_regex)
    start = 0
    has_stt_column = False
    for i, row in enumerate(rows):
        if row and row[0].strip().lower().startswith("stt"):
            start = i + 1
            has_stt_column = True
            break
        if row and row[0].strip().lower().startswith("họ và tên"):
            start = i + 1
            has_stt_column = False
            break
    for row in rows[start:]:
        if has_stt_column:
            if not row or not row[0].strip() or not row[1].strip():
                continue
            stt_str = row[0].strip()
            name = row[1].strip()
            login = row[2].strip() if len(row) > 2 else ""
            note = row[3].strip() if len(row) > 3 else ""
            stt = int(stt_str) if stt_str.isdigit() else None
            m = pat.search(login)
            login_num = int(m.group(1)) if m else None
        else:
            if not row or not row[0].strip():
                continue
            name = row[0].strip()
            login = row[1].strip() if len(row) > 1 else ""
            note = row[3].strip() if len(row) > 3 else (row[2].strip() if len(row) > 2 and not re.search(r"\d{2}/\d{2}/\d{4}|^\d+$", row[2].strip()) else "")
            m = pat.search(login)
            stt = int(m.group(1)) if m else None
            login_num = stt
        roster.append({"stt": stt, "login_num": login_num,
                       "name": re.sub(r"\(.*?\)", "", name).strip(), "note": note})
    for extra in (extra_students or []):
        roster.append({"stt": extra.get("stt"), "login_num": extra.get("login_num", extra.get("stt")),
                       "name": extra["name"], "note": extra.get("note", "")})
    return roster

def guess_id_regex(path, class_name=""):
    """Đoán regex lấy số thứ tự từ "Tên đăng nhập" (vd "a001.x@example.com" → 1).
    Cách 1: theo tên lớp (vd lớp "N42" → n42(\\d+)) nếu đa số tên đăng nhập
    bắt đầu bằng mã đó. Cách 2 (dự phòng): coi 3 chữ số cuối của phần trước
    dấu "." hoặc "@" là số thứ tự. Trả về regex có đúng 1 nhóm bắt số."""
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    delim = ","
    for ln in text.splitlines():
        low = ln.strip().lower()
        if low.startswith("stt") or "họ và tên" in low:
            delim = ";" if ln.count(";") > ln.count(",") else ","
            break
    logins = []
    for row in csv.reader(io.StringIO(text), delimiter=delim):
        for cell in row:
            c = cell.strip()
            if re.match(r"^[A-Za-z]+\d+", c):
                logins.append(re.split(r"[.@]", c)[0].lower())
                break
    cls = re.sub(r"[^a-z0-9]", "", unidecode(class_name or "").lower())
    if cls and logins:
        hits = sum(1 for l in logins if l.startswith(cls))
        if hits >= max(1, len(logins) // 2):
            return re.escape(cls) + r"(\d+)"
    return r"^[A-Za-z]+\d*?(\d{3})(?:\D|$)"

def build_matcher(roster):
    """
    Cơ chế chống nhầm lẫn TỔNG QUÁT (không chỉ áp dụng cho tên trùng y hệt
    trong roster). Bug thực tế từng gặp: sender Zoom tên ngắn/nickname như
    "Khoa Vũ" chấm điểm HOÀ (tie) với NHIỀU roster name khác nhau cùng chứa
    2 từ đó (vd "Vũ Nguyễn Khoa Thành" VÀ "Vũ Minh Khoa" cùng 1.5 điểm) —
    trước đây script chỉ coi là ambiguous khi 2 ứng viên CÙNG TÊN CHỮ, nên bỏ
    sót trường hợp này và âm thầm chọn đại ứng viên đầu tiên trong file roster,
    gộp nhầm tin nhắn của người này vào người khác.

    Cách sửa: với MỌI sender, luôn tính thêm điểm khớp của chính NỘI DUNG tin
    nhắn (nơi học sinh hay tự giới thiệu kiểu "86. Vũ Minh Khoa") với roster.
    Nếu tên Zoom hiển thị bị hoà điểm giữa ≥2 người, hoặc nội dung tin nhắn chỉ
    ra một người khác với độ tin cậy cao hơn hẳn, ưu tiên bằng chứng từ nội
    dung tin nhắn (đáng tin hơn nickname Zoom). Chỉ khi không có gì giải quyết
    được mới đánh dấu ambiguous để con người xác nhận thủ công.

    LƯU Ý (rà soát độ chính xác): hàm này TRƯỚC ĐÂY nhận thêm tham số
    `duplicate_name_hint` (lấy từ fixed_notes) nhưng
    KHÔNG hề dùng nó ở đâu trong thân hàm — dead parameter, tạo cảm giác sai
    rằng khai báo tên trùng ở đó sẽ giúp phân giải, trong khi cơ chế phân giải
    thực tế (STT hint + nội dung tin nhắn tự giới thiệu, xem match() bên dưới)
    đã đủ tổng quát và không cần hint riêng cho từng tên trùng. Đã bỏ tham số
    này (không đổi hành vi, vì nó chưa từng có tác dụng gì). Trường
    `duplicate_name_hint` trong cấu hình lớp vẫn giữ lại làm GHI CHÚ CHO
    NGƯỜI, không phải input cho code.
    """
    roster_norm = [(norm(x["name"]), x) for x in roster]

    def score_all(cand_text):
        """Chấm điểm cand_text với toàn bộ roster, trả về (best_entry, best_score, ties)
        trong đó ties = list các entry đạt best_score (>0).

        QUAN TRỌNG (phát hiện 26/09/2026): so khớp theo từ dưới đây dùng
        Counter (đa tập/multiset), KHÔNG dùng set thường. Lý do: nếu 1 tên có
        2 từ TRÙNG NHAU sau khi bỏ dấu (vd "Trần Trân" -> cả họ và tên đều
        fold về "nguyen", vì "Nguyên" và "Nguyễn" cùng mất dấu về 1 chữ; hay
        "Hồ Trần Bảo Trân" cũng vậy) thì set() SUY BIẾN — gộp 2 từ lặp
        thành còn 1 phần tử, làm sai CẢ tử số lẫn mẫu số bên dưới, dù từ suy
        biến nằm ở phía roster (rtoks) hay phía input (tokens):
          - Suy biến ở rtoks: rtoks co về {"nguyen"}, dễ trở thành TẬP CON của
            gần như MỌI sender họ Nguyễn -> kích hoạt oan bonus subset (+0.5)
            với người không liên quan (ca thực tế: sender mới "Lý Bảo Nam" bị chấm 0.833 và gộp nhầm vào "Trần Trân").
          - Suy biến ở tokens (input): mẫu số `max(len(tokens),1)` sụp xuống
            1, đẩy điểm overlap CƠ BẢN lên tối đa 1.0 với BẤT KỲ roster entry
            nào chỉ cần chứa 1 trong 2 từ đó — cùng cơ chế lỗi, chỉ khác
            chiều, không riêng gì họ Nguyễn.
        Counter không suy biến ở cả 2 chiều: Counter("nguyen nguyen") =
        {"nguyen": 2}, giữ đúng số lần lặp. `(A - B)` (Counter trừ Counter,
        chỉ giữ phần DƯƠNG) rỗng nghĩa là A là "tập con đa tập" của B; tổng
        `(A & B)` (giao đa tập = tổng min theo từng từ) là số từ chồng lấp
        thực — tổng quát hoá đúng "issubset"/"độ chồng lấp" cho cả tên có từ
        lặp, không cần biết từ lặp nằm ở bên nào."""
        n = norm(strip_stt(cand_text))
        tokens = Counter(n.split())
        total = sum(tokens.values())
        if not total:
            return None, 0, []
        best_score = 0
        ties = []
        for rn, entry in roster_norm:
            rtoks = Counter(rn.split())
            overlap = sum((tokens & rtoks).values())
            s = overlap / max(total, 1)
            if _submultiset_either_way(tokens, rtoks):
                s += 0.5
            if n in rn or rn in n:
                s += 0.3
            if n == rn:
                # Khớp Y HỆT TOÀN BỘ tên (không chỉ là 1 phần/tập con của 1 tên
                # dài hơn) là bằng chứng mạnh hơn HẲN so với chỉ chứa 1 phần —
                # phải luôn thắng áp đảo, không được để hoà điểm với ứng viên
                # chỉ khớp kiểu subset/substring. Bug thực tế từng gặp: sender
                # "hà tuấn" trùng Y HỆT tên "Hà Tuấn" nhưng trước đây hoà điểm
                # (1.8 = 1.8) với "Vũ Hà Tuấn" chỉ vì "hà tuấn" cũng là 1 phần
                # của tên đó -> hoà điểm giả tạo, bị đánh dấu ambiguous oan dù
                # thực ra không hề mơ hồ. Nếu ≥2 học sinh CÙNG trùng y hệt 1
                # tên (trùng tên thật, vd 2 bạn cùng tên "Lê Văn Bình"),
                # cả 2 vẫn cùng được +10 nên vẫn hoà đúng nghĩa — chỉ loại bỏ
                # hoà điểm GIẢ TẠO giữa "trùng y hệt" và "chỉ trùng 1 phần".
                s += 10.0
            if s > best_score:
                best_score, ties = s, [entry]
            elif s == best_score and s > 0:
                ties.append(entry)
        best = ties[0] if ties else None
        return best, best_score, ties

    def resolve_ties_by_diacritics(cand_text, ties):
        """PHÁ THẾ HOÀ bằng dấu tiếng Việt — CHỈ gọi khi score_all() đã cho ra
        ≥2 ứng viên hoà điểm ở lớp bỏ dấu phía trên. Ví dụ kinh điển: roster
        có cả "Trần Bảo Ánh" và "Trần Bảo Anh" — 2 tên NHÌN KHÁC NHAU
        nhưng cùng fold về "nguyen ngoc anh" sau khi bỏ dấu, nên hoà điểm
        10.0 = 10.0 (đúng, vì bản đã bỏ dấu thật sự không phân biệt được).
        Nếu cand_text (tên Zoom hoặc câu tự giới thiệu) THỰC SỰ có gõ dấu, đó
        là bằng chứng thêm để phân giải mà không cần đoán — lý do dùng dấu
        làm bằng chứng thay vì bỏ hẳn: bỏ dấu trước khi so khớp làm MẤT một
        nguồn thông tin phân biệt tên rất lớn trong tiếng Việt.

        AN TOÀN LÀ TRÊN HẾT — hàm này chỉ có thể biến 1 ca ambiguous thành
        resolved, KHÔNG BAO GIỜ ngược lại, và KHÔNG BAO GIỜ tự tạo ra 1 ứng
        viên ngoài `ties` (không chấm điểm lại, không mở rộng danh sách):
          - Không gõ dấu (has_diacritics=False, vd nickname "anh" trơn) ->
            không có bằng chứng gì để dùng -> trả None ngay, giữ nguyên
            ambiguous như cũ. Rất nhiều học sinh không gõ dấu khi tự điền
            tên, nên đây là điều kiện bắt buộc, không phải tuỳ chọn.
          - Có gõ dấu nhưng khớp ĐỒNG THỜI ≥2 ứng viên (hoặc 0 ứng viên,
            vd gõ sai dấu / thiếu 1 từ) -> vẫn không đủ để phân giải chắc
            chắn -> trả None, vẫn ambiguous, TUYỆT ĐỐI không đoán.
          - Chỉ trả về 1 entry khi ĐÚNG 1 ứng viên trong `ties` thoả sub-multiset
            (Counter trừ nhau ra rỗng) với cand_text ở ÍT NHẤT 1 TRONG 2
            CHIỀU — cùng phép dùng trong bonus subset của score_all ở trên,
            để nhất quán logic: chiều "cand ⊆ entry" xử lý ca nickname NGẮN
            hơn tên roster (vd chỉ gõ "Ánh"); chiều "entry ⊆ cand" xử lý ca
            câu tự giới thiệu DÀI hơn tên roster (tên nằm lọt trong 1 câu dài
            hơn, vd "Mình là Trần Bảo Ánh nhé mọi người")."""
        if len(ties) < 2 or not has_diacritics(cand_text):
            return None
        cand_tokens = Counter(norm_vn(strip_stt(cand_text)).split())
        if not cand_tokens:
            return None
        # Kiểm tra CẢ 2 CHIỀU (giống hệt phép subset bonus trong score_all):
        # (a) cand ⊆ entry — ca nickname NGẮN hơn tên roster (vd chỉ gõ "Ánh");
        # (b) entry ⊆ cand — ca câu tự giới thiệu DÀI hơn tên roster (tên nằm
        # lọt trong 1 câu dài, vd "Mình là Trần Bảo Ánh nhé mọi người").
        # Thiếu chiều (b) sẽ bỏ sót đúng ca quan trọng nhất (câu tự giới thiệu
        # tự nhiên luôn có thêm từ khác quanh tên) — chỉ chiều (a) mới đủ dùng
        # cho ca nickname trơn, không đủ cho ca còn lại.
        matches = []
        for e in ties:
            entry_tokens = Counter(norm_vn(e["name"]).split())
            if _submultiset_either_way(cand_tokens, entry_tokens):
                matches.append(e)
        return matches[0] if len(matches) == 1 else None

    def match(raw_name, message_texts=None):
        """
        raw_name       : tên sender lấy từ Zoom chat (có thể là nickname, vd "Khoa Vũ").
        message_texts  : nội dung các tin nhắn của sender này — nguồn bằng chứng
                          đáng tin hơn tên Zoom vì học sinh hay tự giới thiệu
                          đầy đủ kiểu "86. Vũ Minh Khoa" ngay trong tin đầu.
        Trả về (entry, score, ambiguous). Khi ambiguous=True, entry LUÔN là
        None (không đoán đại 1 trong số các ứng viên hoà điểm) — nơi gọi phải
        tự coi đây là "chưa khớp" nếu cần đưa vào danh sách cần xác nhận thủ
        công, KHÔNG được cộng dồn tin nhắn/điểm cho ai trong trường hợp này.
        """
        message_texts = message_texts or []
        raw_best, raw_score, raw_ties = score_all(raw_name)

        # QUAN TRỌNG: kiểm tra STT hint TRƯỚC khi áp ngưỡng 0.5 phía dưới — nếu
        # làm ngược lại (như bản cũ), 1 ca hoà điểm ĐÚNG NGƯỠNG 0.5 (vd nickname
        # 2 từ chỉ trùng 1 từ với nhiều học sinh) sẽ bị xoá sạch raw_ties trước
        # khi kịp dùng STT để phân giải, dù STT (số tự học sinh gõ kèm tên, vd
        # "23. Thái Bảo") là bằng chứng đáng tin hơn NHIỀU so với điểm khớp từ —
        # bỏ lỡ 1 cơ hội phân giải chắc chắn chỉ vì ngưỡng chấm điểm chung.
        stt_hint = extract_leading_stt(raw_name)
        if stt_hint is not None and len(raw_ties) > 1:
            pick = pick_by_stt(raw_ties, stt_hint)
            if pick is not None:
                return pick, raw_score, False

        # Cùng vị trí ưu tiên như STT hint ở trên (TRƯỚC ngưỡng 0.5 phía dưới):
        # nếu tên Zoom hiển thị CÓ gõ dấu và dấu đó khớp đúng 1 trong các ứng
        # viên đang hoà điểm, coi là bằng chứng đủ tin cậy để phân giải luôn
        # (xem resolve_ties_by_diacritics để biết vì sao an toàn — không gõ
        # dấu hoặc dấu không khớp rõ ràng thì trả None, rơi xuống xử lý cũ).
        if len(raw_ties) > 1:
            pick = resolve_ties_by_diacritics(raw_name, raw_ties)
            if pick is not None:
                return pick, raw_score, False

        # điểm <=0.5 chỉ đến từ trùng lẻ tẻ 1 từ chung chung (vd "Nguyễn") mà
        # KHÔNG chứa trọn tên ứng viên -> không đủ tin cậy để coi là có match
        if raw_score <= 0.5:
            raw_best, raw_score, raw_ties = None, 0, []

        # Tìm bằng chứng tốt nhất từ nội dung tin nhắn (thường là câu tự giới thiệu).
        # LƯU Ý: 1 tin nhắn nhắc tên NGƯỜI KHÁC (vd báo hộ bạn cùng lớp vừa vào
        # muộn) cũng có thể trùng token với 1 tên trong roster — để tránh nhầm
        # "tự giới thiệu" với "nhắc tên người khác", chỉ chấp nhận bằng chứng
        # từ tin nhắn khi độ khớp đạt >=1.0 (tức khớp TRỌN VẸN tên, không phải
        # chỉ khớp vài từ rời rạc).
        text_best, text_score, text_ties = None, 0, []
        for t in message_texts:
            b, s, ties = score_all(t)
            if b and s > text_score:
                text_best, text_score, text_ties = b, s, ties
                stt_in_text = extract_leading_stt(t)
                if stt_in_text is not None and len(ties) > 1:
                    pick = pick_by_stt(ties, stt_in_text)
                    if pick is not None:
                        text_best, text_score, text_ties = pick, s, [pick]
                if len(text_ties) > 1:
                    pick = resolve_ties_by_diacritics(t, text_ties)
                    if pick is not None:
                        text_best, text_score, text_ties = pick, s, [pick]

        raw_unique = len(raw_ties) == 1
        text_unique = len(text_ties) == 1
        text_confident = text_unique and text_score >= 1.0

        if raw_unique:
            if text_confident and text_best is not raw_ties[0] and text_score >= raw_score + 0.3:
                return text_best, text_score, False
            return raw_ties[0], raw_score, False

        if text_confident:
            # Tên Zoom không rõ ràng (hoà điểm hoặc dưới ngưỡng), nhưng nội dung
            # tin nhắn tự giới thiệu khớp TRỌN VẸN 1 người duy nhất -> tin cậy
            return text_best, text_score, False

        if raw_ties:
            # Tên Zoom hoà điểm giữa ≥2 người, tin nhắn cũng không đủ tin cậy để
            # giải quyết -> KHÔNG âm thầm chọn đại VÀ KHÔNG cộng dồn tin nhắn/
            # lượt phát biểu cho bất kỳ ai trong số họ. Bug thực tế từng gặp:
            # sender "Phong" hoà điểm giữa 2 học sinh (1 đang học, 1 đã nghỉ
            # hẳn) nhưng vẫn bị cộng thẳng 15 tin + 11 lượt phát biểu cho người
            # được chọn đại `raw_ties[0]`, làm sai cả bảng xếp hạng lẫn phân
            # loại tương tác của người đó — nơi gọi chỉ cảnh báo cuối báo cáo
            # ("đã tạm gán") chứ không hề ngăn việc cộng nhầm điểm/tin. Trả về
            # entry=None để nơi gọi coi đây là "chưa khớp" (unmatched, sẽ lên
            # đúng mục "người lạ"/"chưa khớp chắc chắn" để xác nhận thủ công)
            # — an toàn hơn đoán bừa 1 người. Vẫn trả ambiguous=True để phân
            # biệt với "hoàn toàn không khớp ai" (raw_score=0) nếu sau này cần
            # xử lý khác nhau giữa 2 trường hợp.
            return None, raw_score, True

        return None, 0, False

    return match

# ======================= ĐỌC PARTICIPANTS =======================
def load_participants(path):
    """Đọc file 'Save Participants' của Zoom, giữ lại tên + TỔNG THỜI GIAN
    tham gia (phút). Chấp nhận nhiều biến thể tên cột (Zoom có thể xuất tiếng
    Việt hoặc tiếng Anh tuỳ region) để không vỡ khi định dạng export khác đi."""
    # So khớp tên cột KHÔNG phân biệt hoa/thường: Zoom xuất "Name (original name)"
    # (chữ thường) ở 1 số vùng/thời điểm, khác với "Name (Original Name)" đã
    # khai trước đây — so khớp phân biệt hoa/thường cũ khiến TOÀN BỘ file bị bỏ
    # qua (0 dòng, không báo lỗi gì) mỗi khi header đúng dạng chữ thường này.
    name_keys = ["Tên (tên gốc)", "Name (Original Name)", "Tên", "Name"]
    minute_keys = ["Tổng thời gian (phút)", "Total Duration (Minutes)", "Duration (Minutes)"]
    guest_keys = ["Khách", "Guest"]
    name_keys_norm = {k.lower() for k in name_keys}
    minute_keys_norm = {k.lower() for k in minute_keys}
    guest_keys_norm = {k.lower() for k in guest_keys}
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header_map = {(h or "").lower(): h for h in (reader.fieldnames or [])}
        name_col = next((header_map[k] for k in name_keys_norm if k in header_map), None)
        minute_col = next((header_map[k] for k in minute_keys_norm if k in header_map), None)
        guest_col = next((header_map[k] for k in guest_keys_norm if k in header_map), None)
        for row in reader:
            nm = (row.get(name_col, "") or "").strip() if name_col else ""
            if not nm:
                continue
            raw_minutes = (row.get(minute_col, "") or "0") if minute_col else "0"
            try:
                minutes = float(str(raw_minutes).strip().replace(",", "."))
            except ValueError:
                minutes = 0.0
            guest_raw = (row.get(guest_col, "") or "") if guest_col else ""
            rows.append({"raw_name": nm, "minutes": minutes, "guest": guest_raw.strip()})
    return rows

# ======================= ĐỌC & ĐẾM CHAT =======================
# Hỗ trợ đa ngôn ngữ Zoom (Vấn đề 4 trong tài liệu cải tiến): tiếng Anh
# "From ... to ...:" và tiếng Việt "Từ ... tới/đến ...:". LƯU Ý: biến thể
# tiếng Việt CHƯA được kiểm chứng bằng file mẫu thật, chỉ là suy đoán hợp lý —
# nếu gặp file tiếng Việt bị đọc sai/bỏ sót, xin người dùng 1 dòng tiêu đề
# thật để chỉnh lại chính xác thay vì tiếp tục đoán.
HEADER_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (?:From|Từ) (.+?) (?:to|tới|đến) (.+):$"
)

def _canonical_receiver(raw_receiver):
    """Chuẩn hoá 'người nhận' về 1 dạng chung nếu thuộc nhóm 'gửi cho mọi
    người' (Everyone / Mọi người, bất kể hoa-thường hay ngôn ngữ) — để khi GỘP
    nhiều file chat cùng 1 buổi xuất ra ở 2 ngôn ngữ Zoom khác nhau (Vấn đề 4),
    2 tin công khai giống hệt nhau vẫn được nhận diện là CÙNG 1 tin (không đếm
    đúp), thay vì bị coi là khác nhau chỉ vì khác chữ ghi người nhận."""
    n = norm(raw_receiver)
    if n == "everyone" or "moi nguoi" in n:
        return "__EVERYONE__"
    return n

def _is_name_like_segment(seg):
    """True nếu seg trông giống 1 cái tên người (mỗi từ viết hoa chữ cái đầu),
    dùng để nhận diện các đoạn trong danh sách người thả reaction — KHÔNG dùng
    regex ký tự Latin cứng (vì tên tiếng Việt có dấu nằm ngoài range ASCII),
    mà dùng str.isupper() để không phụ thuộc bảng mã."""
    s = strip_stt(seg).strip()
    if not s:
        return False
    words = s.split()
    if not words:
        return False
    for w in words:
        core = w.strip(".-")
        if core and not core[0].isupper():
            return False
    return True

def _looks_like_real_emoji(s):
    """True nếu chuỗi chứa ít nhất 1 ký tự Unicode 'cao' (>= U+2600) — phân biệt
    emoji thật (👍 U+1F44D, 😂 U+1F602, ❤ U+2764...) với icon kiểu ASCII thường
    (':)', ':(' — toàn ký tự < U+0080), để không lọc nhầm 1 câu chat thật kết
    thúc bằng icon kiểu gõ tay."""
    return any(ord(ch) >= 0x2600 for ch in s)

def _is_name_like_segment_relaxed(seg):
    """Bản NỚI của _is_name_like_segment(): KHÔNG yêu cầu viết hoa chữ đầu, vì tên
    hiển thị Zoom rất hay viết thường ("Thu Hà anh", "91 ngô đình khôi",
    "111. vũ hoàng nam"). Chỉ chấp nhận đoạn trông như 1 cái tên/nickname
    ngắn: ≤6 từ, chỉ gồm chữ/số/dấu chấm/gạch/ngoặc/nháy đơn, không có dấu câu
    của 1 câu văn (? ! , ; : ...). Chỉ được dùng cho dòng nằm SAU nội dung tin
    nhắn (xem tham số after_content của _is_reaction_line)."""
    s = seg.strip()
    if not s or len(s.split()) > 6:
        return False
    if not re.fullmatch(r"[\w .\-()\']+", s):
        return False
    return any(ch.isalpha() for ch in s)

def _is_reaction_line(line, after_content=False):
    """
    Nhận diện dòng kiểu "Khoa Vũ, Vi Hoàng Khôi, 79.Cù Xuân Hòa:👍" — đây là
    dòng liệt kê NHỮNG AI ĐÃ THẢ CẢM XÚC cho 1 tin nhắn (do Zoom xuất ra liền
    sau tin nhắn gốc, không có header riêng), KHÔNG PHẢI nội dung người gửi tự
    viết. Nếu không lọc dòng này, tên học sinh khác bị lẫn vào nội dung tin
    nhắn của người gửi hiện tại, khiến việc so khớp tên (dựa vào nội dung tin
    nhắn tự giới thiệu) bị nhầm sang người được nhắc tên trong danh sách thả
    cảm xúc, thay vì người thực sự đang gửi tin.

    Yêu cầu ĐỒNG THỜI 2 điều kiện (an toàn gấp đôi, tránh lọc nhầm tin thật):
    (a) phần sau dấu ':' cuối cùng là emoji Unicode thật — không phải icon
        gõ tay kiểu ':)' hay dấu câu thường; và
    (b) mọi đoạn trước dấu phẩy trông giống 1 cái tên người (viết hoa chữ đầu).
    """
    line = line.strip()
    if ":" not in line:
        return False
    idx = line.rfind(":")
    namepart, emoji = line[:idx], line[idx + 1:].strip()
    if not emoji or len(emoji) > 6 or any(ch.isalnum() for ch in emoji):
        return False
    if not _looks_like_real_emoji(emoji):
        return False
    segs = [s.strip() for s in namepart.split(",") if s.strip()]
    if not segs:
        return False
    if all(_is_name_like_segment(s) for s in segs):
        return True
    # LỖI THỰC TẾ (R73 20/09/2026, ca Vũ Hà Tuấn): tên người thả reaction viết
    # THƯỜNG ("Thu Hà anh:❤️", "91 ngô đình khôi:🏳️‍🌈") không qua được điều kiện
    # (b) ở trên, nên các dòng reaction lọt vào nội dung tin nhắn "30. Vũ Hà Tuấn"
    # -> không còn nhận ra là tin điểm danh. Nếu dòng này nằm SAU nội dung tin
    # (Zoom luôn xuất reaction SAU tin gốc, dòng đầu tiên của 1 tin không bao
    # giờ là reaction), nới điều kiện (b) cho tên viết thường. Điều kiện (a)
    # (emoji Unicode thật, không có chữ/số sau dấu ':') vẫn bắt buộc.
    if after_content:
        return all(_is_name_like_segment_relaxed(s) for s in segs)
    return False

def _parse_chat_file(path):
    """Đọc THÔ 1 file chat Zoom, trả về (entries, guessed_date) với entries là
    list (timestamp_str, sender, receiver_raw, msg_lines) — CHƯA lọc giáo
    viên, CHƯA đếm. Dùng làm input cho merge_chat_entries() khi có nhiều file
    của cùng 1 buổi (Vấn đề 1)."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    lines = raw.replace("\r\n", "\n").split("\n")
    entries = []
    cur = None  # [timestamp, sender, receiver, msg_lines]
    guessed_date = None
    for line in lines:
        m = HEADER_RE.match(line)
        if m:
            ts, sender, receiver = m.group(1), m.group(2).strip(), m.group(3).strip()
            if guessed_date is None:
                dm = re.match(r"^(\d{4})-(\d{2})-(\d{2})", ts)
                if dm:
                    guessed_date = f"{dm.group(3)}/{dm.group(2)}/{dm.group(1)}"
            if cur is not None:
                entries.append(tuple(cur))
            cur = [ts, sender, receiver, []]
        elif cur is not None:
            l = line.strip("\t")
            if l.strip() and _is_reaction_line(
                    l, after_content=any(x.strip() for x in cur[3])):
                continue  # bỏ dòng danh sách người thả reaction, không tính là nội dung
            cur[3].append(l)
    if cur is not None:
        entries.append(tuple(cur))
    return entries, guessed_date

def _cluster_chat_entries(all_raw_entries, time_tolerance_seconds=2):
    """Lõi dùng CHUNG cho merge_chat_entries() và load_chat_timestamped(): gộp
    entries thô từ 1 hoặc NHIỀU file chat của CÙNG 1 buổi theo (người gửi đã
    chuẩn hoá, người nhận đã chuẩn hoá, nội dung), rồi trong mỗi nhóm đó, sắp
    các mốc thời gian tăng dần và GỘP CÁC MỐC LIỀN KỀ cách nhau ≤
    `time_tolerance_seconds` giây thành 1 tin duy nhất (kiểu "gộp chuỗi" — so
    khoảng cách giữa 2 mốc LIÊN TIẾP, không so mốc đầu nhóm — xem giải thích
    đầy đủ + ca lỗi thực tế trong docstring merge_chat_entries()).

    Viết thành 1 hàm lõi riêng (thay vì để load_chat_timestamped() tự viết lại
    logic loại trùng, hoặc suy ngược timestamp từ kết quả của
    merge_chat_entries() bằng cách so khớp lại (sender, content) — cách suy
    ngược này SAI khi cùng 1 người nói CÙNG 1 CÂU ≥2 lần thật sự cách xa nhau
    trong buổi, vd "hiểu ạ" lúc phút 5 và lại "hiểu ạ" lúc phút 40: so khớp lại
    theo (sender, content) sẽ trả về TẤT CẢ các mốc trùng nội dung đó, không
    phân biệt được mốc nào ứng với cụm nào) — đảm bảo 2 hàm gọi nơi cần
    timestamp và nơi không cần đều lấy đúng 1 mốc DUY NHẤT cho mỗi cụm đã gộp:
    mốc SỚM NHẤT trong cụm (thời điểm tin nhắn thực sự được gửi lần đầu, trước
    khi bị lệch giây ở file export sau).

    Trả về: list (dt: datetime của mốc sớm nhất trong cụm, sender, receiver_raw,
    msg_lines) đã sắp theo thời gian."""
    ordered = sorted(all_raw_entries, key=lambda e: e[0])

    def _ts_to_dt(ts):
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")

    groups = defaultdict(list)
    for idx, (ts, sender, receiver, msg_lines) in enumerate(ordered):
        content = "\n".join(l.strip() for l in msg_lines).strip()
        key = (norm(sender), _canonical_receiver(receiver), content)
        groups[key].append((_ts_to_dt(ts), idx))

    keep_idx = set()
    for key, items in groups.items():
        items.sort(key=lambda p: p[0])
        prev_dt = None
        for dt, idx in items:
            if prev_dt is None or (dt - prev_dt).total_seconds() > time_tolerance_seconds:
                keep_idx.add(idx)
            prev_dt = dt

    return [(_ts_to_dt(ordered[idx][0]), ordered[idx][1], ordered[idx][2], ordered[idx][3])
            for idx in sorted(keep_idx, key=lambda i: ordered[i][0])]


def merge_chat_entries(all_raw_entries, time_tolerance_seconds=2):
    """Gộp entries thô từ 1 hoặc NHIỀU file chat của CÙNG 1 buổi (Vấn đề 1, 4,
    5 trong tài liệu cải tiến): chỉ giữ 1 bản cho mỗi tin công khai trùng giữa
    các file (không đếm đúp), trong khi tin RIÊNG (khác người nhận ở mỗi file,
    Vấn đề 1) và tin công khai bị THIẾU ở file của người join muộn (Vấn đề 5)
    đều tự động được giữ đủ vì không trùng khoá với gì đã thấy — không cần
    logic riêng cho từng trường hợp.

    QUAN TRỌNG (lỗi thực tế phát hiện 16/09/2026, ca "Phùng Việt Anh"/"Kiều Đức Hải" bị đếm THỪA 1 tin mỗi người): bản trước so khớp khoá trùng bằng
    MỐC THỜI GIAN Y HỆT (tới từng giây). Nhưng khi 2 người khác nhau tự export
    file chat của CÙNG 1 buổi, đồng hồ mỗi máy/mỗi lần Zoom ghi lại mốc giây
    của CÙNG 1 tin nhắn công khai có thể LỆCH NHAU 1-2 GIÂY (vd file A ghi
    21:44:56, file B ghi 21:44:57 cho cùng đúng 1 tin "A+C") — chỉ lệch 1 giây
    cũng đủ khiến so khớp y hệt thất bại, khiến tin đó bị đếm THÀNH 2 tin dù
    học sinh chỉ gửi 1 lần. Việc này xảy ra ở diện RỘNG (không chỉ 2 ca trên)
    bất cứ khi nào gộp ≥2 file chat của cùng 1 buổi. Ca thực tế đã kiểm chứng:
    "Hải Long Cấn" nói "hiểu ạ" 7 lần theo mốc thời gian thô gộp từ 2 file,
    nhưng chỉ có 5 CỤM thời gian thực sự tách biệt — 2 cặp còn lại là do lệch
    đồng hồ giữa 2 file cho cùng 1 lần nói, đã gộp đúng thành 5 tin. Đã sửa
    bằng cách gộp chuỗi theo thời gian (xem _cluster_chat_entries()).

    Trả về: list (sender, msg_lines) đã sắp theo thời gian, sẵn sàng đếm."""
    clustered = _cluster_chat_entries(all_raw_entries, time_tolerance_seconds)
    return [(sender, msg_lines) for _dt, sender, _receiver, msg_lines in clustered]

def load_chat(chat_txt, extra_teacher_names=None):
    """
    chat_txt : 1 đường dẫn (str) HOẶC list nhiều đường dẫn — nhiều file chat
    của CÙNG 1 buổi học. Zoom cho phép nhiều người (học sinh/GV) tự export
    file chat riêng: phần tin CÔNG KHAI giống hệt nhau giữa các file nhưng
    phần tin RIÊNG thì khác nhau (mỗi file chỉ có tin riêng liên quan trực
    tiếp người export) — và file của người join muộn còn THIẾU tin công khai
    đầu buổi. Gộp nhiều file giúp lấy đủ dữ liệu mà KHÔNG đếm đúp phần trùng
    (xem merge_chat_entries). Tin nhắn riêng vẫn cộng CHUNG vào tổng "Số tin"
    như tin công khai, không tách cột riêng (quyết định chủ động của người
    dùng, xem Vấn đề 2 — không tự ý đổi nếu chưa hỏi lại).
    """
    paths = [chat_txt] if isinstance(chat_txt, str) else list(chat_txt)
    all_raw = []
    for path in paths:
        entries, _gd = _parse_chat_file(path)
        all_raw.extend(entries)

    # Ngày đoán = ngày của mốc thời gian SỚM NHẤT trong toàn bộ các file gộp
    # lại (không chỉ dòng đầu của 1 file — file join muộn có thể bắt đầu
    # muộn hơn file khác của cùng buổi).
    guessed_date = None
    if all_raw:
        earliest_ts = min(e[0] for e in all_raw)
        dm = re.match(r"^(\d{4})-(\d{2})-(\d{2})", earliest_ts)
        if dm:
            guessed_date = f"{dm.group(3)}/{dm.group(2)}/{dm.group(1)}"

    merged_entries = merge_chat_entries(all_raw)

    counts, texts = defaultdict(int), defaultdict(list)
    for sender, msg_lines in merged_entries:
        if is_teacher(sender, extra_teacher_names):
            continue
        text = " ".join(l for l in msg_lines if l.strip())
        if not text.strip():
            continue
        counts[sender] += 1
        texts[sender].append(text)
    return counts, texts, guessed_date

# =============== ĐỌC FILE TRANSCRIPT (.vtt) — LƯỢT PHÁT BIỂU BẰNG GIỌNG NÓI ===============
VTT_CUE_TIME_RE = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}")

def load_vtt_transcript(path, extra_teacher_names=None):
    """
    Đọc file transcript .vtt tự động do Zoom cloud recording tạo (Vấn đề 3
    trong tài liệu cải tiến): bắt đầu bằng WEBVTT, mỗi 'cue' gồm số thứ tự +
    khoảng thời gian (hh:mm:ss.mmm --> hh:mm:ss.mmm) + đúng 1 dòng
    'Tên người nói: nội dung' + 1 dòng trống. Mỗi cue = 1 LƯỢT PHÁT BIỂU.

    Dùng để bắt các lượt tương tác bằng giọng nói (mở mic, không chat) mà
    trước đây bị bỏ sót hoàn toàn nếu chỉ đọc file chat.

    Trả về dict {tên người nói (tên gốc trong file): số lượt phát biểu} — đã
    loại giảng viên/TG bằng is_teacher() (CÙNG cơ chế đang dùng cho chat).
    Việc đối chiếu tên người nói với danh sách lớp do build_ranking_table làm,
    dùng ĐÚNG matcher chung với chat (không viết cơ chế khớp tên riêng thứ 2).

    Cue không tách được rõ 'Tên: nội dung' (vd thiếu dấu ':') sẽ bị bỏ qua,
    không đếm — thà bỏ sót còn hơn đoán bừa.
    """
    with open(path, encoding="utf-8-sig") as f:
        raw = f.read()
    lines = raw.replace("\r\n", "\n").split("\n")

    turn_counts = defaultdict(int)
    i, n = 0, len(lines)
    while i < n:
        if VTT_CUE_TIME_RE.search(lines[i]):
            i += 1
            content_lines = []
            while i < n and lines[i].strip():
                content_lines.append(lines[i].strip())
                i += 1
            full = " ".join(content_lines).strip()
            m = re.match(r"^(.+?):\s?(.*)$", full)
            if m:
                speaker = m.group(1).strip()
                if speaker and not is_teacher(speaker, extra_teacher_names):
                    turn_counts[speaker] += 1
        else:
            i += 1
    return dict(turn_counts)

# ============ GỌI TÊN BẰNG GIỌNG NÓI NHƯNG KHÔNG THẤY PHẢN HỒI (tuỳ chọn) ============
# Tính năng này CHỈ hoạt động khi có file .vtt (chỉ giọng nói mới ghi lại được
# việc GV gọi tên miệng — chat không có thông tin này). Đây LUÔN LUÔN là suy
# đoán để LIỆT KÊ NGHI VẤN cho trợ giảng tự xác nhận bằng mắt/tai, KHÔNG BAO
# GIỜ tự động trừ điểm tương tác — chỉ trừ khi trợ giảng xác nhận lại bằng
# tham số `mic_call_confirmed_penalty` ở lần chạy sau (xem generate_report).
VTT_CUE_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})"
)

MIC_EXCUSE_KEYWORDS_NORM = [norm(k) for k in [
    "hong mic", "hu mic", "mic hong", "mic hu", "mic bi hong", "mic bi hu",
    "loi mic", "mic loi", "mic bi loi", "mic khong hoat dong",
    "khong mo mic duoc", "khong bat mic duoc", "mat mic", "rot mic",
    "mic co van de", "khong noi duoc", "hong tai nghe", "hu tai nghe",
    "mic khong len tieng", "mic khong vao",
]]

def _has_mic_excuse(text):
    """True nếu tin nhắn chứa từ khoá xin phép không mở mic (hỏng/lỗi mic...),
    dùng để tự động loại 1 ca nghi vấn nếu học sinh đã tự giải thích qua chat."""
    t = norm(text)
    return any(k in t for k in MIC_EXCUSE_KEYWORDS_NORM)

def _vtt_ts_to_seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

def _fmt_vtt_time(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"

def parse_vtt_cues(path):
    """Đọc TOÀN BỘ cue trong file .vtt theo ĐÚNG THỨ TỰ THỜI GIAN, giữ lại mốc
    thời gian bắt đầu (giây, tính từ đầu file ghi hình) của từng lượt nói —
    khác với load_vtt_transcript() (chỉ đếm TỔNG số lượt, không cần biết THỜI
    ĐIỂM). Dùng riêng cho tính năng phát hiện gọi tên không trả lời, không
    thay thế/đổi hành vi của load_vtt_transcript() đang chạy ổn định.
    Trả về list dict {start (giây), speaker (tên gốc), content (nội dung)}.
    """
    with open(path, encoding="utf-8-sig") as f:
        raw = f.read()
    lines = raw.replace("\r\n", "\n").split("\n")
    cues = []
    i, n = 0, len(lines)
    while i < n:
        m = VTT_CUE_RE.search(lines[i])
        if m:
            start = _vtt_ts_to_seconds(*m.groups()[0:4])
            end = _vtt_ts_to_seconds(*m.groups()[4:8])
            i += 1
            content_lines = []
            while i < n and lines[i].strip():
                content_lines.append(lines[i].strip())
                i += 1
            full = " ".join(content_lines).strip()
            mm = re.match(r"^(.+?):\s?(.*)$", full)
            if mm:
                speaker, content = mm.group(1).strip(), mm.group(2).strip()
                cues.append({"start": start, "end": end, "speaker": speaker, "content": content})
        else:
            i += 1
    return cues

# ============ TỰ NHẬN DIỆN GIẢNG VIÊN TỪ FILE .VTT (dựa vào mức độ áp đảo) ============
def compute_speaker_stats_from_vtt(cues):
    """cues: list từ parse_vtt_cues() (CHƯA loại GV/TG nào, dùng cues thô).
    Trả về dict {tên người nói gốc: {"turns": số lượt, "duration": tổng giây}}.
    """
    stats = defaultdict(lambda: {"turns": 0, "duration": 0.0})
    for c in cues:
        d = stats[c["speaker"]]
        d["turns"] += 1
        d["duration"] += max(0.0, c.get("end", c["start"]) - c["start"])
    return dict(stats)

def detect_teacher_from_vtt_dominance(cues, known_teacher_names=None):
    """
    Tự nhận diện giảng viên (TG/GV) trong buổi học chỉ từ file .vtt, KHÔNG cần
    trợ giảng khai tay `extra_teacher_names`: GV luôn là người có SỐ LƯỢT PHÁT
    BIỂU NHIỀU NHẤT VÀ TỔNG THỜI LƯỢNG PHÁT BIỂU NHIỀU NHẤT trong buổi (giảng
    bài liên tục suốt buổi), khác hẳn học sinh dù tương tác tốt nhất cũng chỉ
    phát biểu ngắn, rời rạc.

    Ca thực tế từng gặp: người nói "Bạch Đức Long" (GV) có 700 lượt phát
    biểu bị khớp nhầm vào 1 học sinh trùng vài âm tiết tên, chiếm luôn rank 1
    bảng xếp hạng — nếu tự nhận diện được ngay từ đầu sẽ tránh được lỗi này mà
    không cần đợi trợ giảng phát hiện thủ công.

    CHỈ tự động kết luận khi 2 điều kiện (nhiều lượt nhất VÀ nhiều thời lượng
    nhất) cùng rơi vào ĐÚNG 1 người — nếu 2 "quán quân" này là 2 người khác
    nhau thì KHÔNG đủ chắc chắn để tự đoán, trả về None (im lặng, để trợ giảng
    tự khai `extra_teacher_names` nếu cần — không đoán bừa).

    Bug thực tế từng gặp: khi GV thật ĐÃ được loại đúng từ trước qua marker cố
    định (`is_teacher`, vd tên hiển thị "Gv.Hải"), hàm vẫn tiếp tục tìm "quán
    quân" trong số CÒN LẠI (toàn học sinh) mà KHÔNG có ngưỡng tối thiểu nào —
    nên chọn đại 1 học sinh tương tác thoại nhiều nhất trong nhóm còn lại (dù
    chỉ 16 lượt/42 giây trên cả buổi hàng giờ đồng hồ) làm "GV thứ 2", dù rõ
    ràng không phải GV. Sửa: GV giảng bài liên tục nên luôn chiếm PHẦN LỚN
    tổng lượt/tổng thời lượng nói của CẢ BUỔI (tính trên toàn bộ `stats`, kể cả
    GV đã bị loại) — một học sinh dù tương tác nhiều nhất trong nhóm còn lại
    vẫn chỉ chiếm 1 phần rất nhỏ của tổng đó. Yêu cầu thêm: ứng viên phải đạt
    tối thiểu 1 ngưỡng TƯƠNG ĐỐI (tỉ lệ % so với tổng cả buổi) VÀ 1 ngưỡng
    TUYỆT ĐỐI (số lượt/số giây tối thiểu, đề phòng buổi quá ngắn khiến tỉ lệ %
    dễ đạt dù số tuyệt đối vẫn nhỏ) — không đạt đủ cả 2 thì trả về None thay vì
    đoán bừa. Đồng thời có tác dụng phụ đúng ý muốn: nếu GV thật ĐÃ bị loại qua
    marker cố định, phần còn lại gần như chắc chắn không ai đạt nổi ngưỡng %
    này nữa, nên hàm sẽ tự động không cố tìm "GV thứ 2" — không cần viết riêng
    1 điều kiện "đã có GV từ marker thì dừng".

    Trả về (tên_người_nói, số_lượt, tổng_giây) hoặc None.
    """
    MIN_SHARE = 0.30       # phải chiếm tối thiểu 30% tổng lượt/thời lượng nói cả buổi
    MIN_ABS_TURNS = 30     # và tối thiểu 30 lượt phát biểu
    MIN_ABS_DURATION = 300  # và tối thiểu 300 giây (5 phút) tổng thời lượng nói

    stats = compute_speaker_stats_from_vtt(cues)
    known = known_teacher_names or []
    candidates = {s: v for s, v in stats.items() if not is_teacher(s, known)}
    if not candidates:
        return None
    total_turns = sum(v["turns"] for v in stats.values())
    total_duration = sum(v["duration"] for v in stats.values())
    if total_turns <= 0 or total_duration <= 0:
        return None
    top_turns_name = max(candidates.items(), key=lambda kv: kv[1]["turns"])[0]
    top_duration_name = max(candidates.items(), key=lambda kv: kv[1]["duration"])[0]
    if top_turns_name != top_duration_name:
        return None
    top = candidates[top_turns_name]
    if (top["turns"] < MIN_ABS_TURNS or top["duration"] < MIN_ABS_DURATION or
            top["turns"] / total_turns < MIN_SHARE or
            top["duration"] / total_duration < MIN_SHARE):
        return None
    return top_turns_name, top["turns"], top["duration"]

def _name_last_n_tokens(name_norm, k):
    toks = name_norm.split()
    return " ".join(toks[-k:]) if len(toks) >= k else None

def collect_known_aliases(roster_full, extra_teacher_names, sender_resolution, all_cues, match):
    """
    Thu thập các "alias" (tên gọi khác) đã biết CHẮC CHẮN của từng học sinh
    TRONG CHÍNH BUỔI HỌC NÀY — vì GV nhiều khi gọi tên theo TÊN HIỂN THỊ ZOOM
    (nickname, vd "Khoa Vũ") thay vì tên thật trong danh sách lớp (vd "Vũ Minh Khoa"), nên nếu chỉ dò theo tên thật sẽ bỏ sót các ca này.

    Nguồn alias (CHỈ lấy khi đã khớp KHÔNG mơ hồ — score>=0.5 và not ambiguous,
    dùng ĐÚNG `match` đang dùng cho chat/.vtt, không đoán bừa):
    - Tên thật trong roster (luôn có, để không mất hành vi gốc).
    - Tên hiển thị Zoom trong chat của chính học sinh đó (từ `sender_resolution`
      — kết quả ghép tên đã có sẵn từ build_ranking_table, không ghép lại).
    - Tên hiển thị Zoom khi chính học sinh đó phát biểu trong `.vtt` (nếu có).

    Trả về dict: entry_key(entry) -> {"entry": entry, "aliases": {alias_text: source}},
    source là "real_name" (tên thật, luôn ưu tiên giữ nếu trùng) hoặc "observed"
    (nickname Zoom quan sát được trong buổi).
    """
    alias_map = {}
    def _add(entry, alias, source):
        if not alias:
            return
        k = entry_key(entry)
        d = alias_map.setdefault(k, {"entry": entry, "aliases": {}})
        if alias not in d["aliases"] or source == "real_name":
            d["aliases"][alias] = source

    for e in roster_full:
        _add(e, e["name"], "real_name")
    for sender, (m, amb) in (sender_resolution or {}).items():
        if m and not amb:
            _add(m, sender, "observed")
    for cue in (all_cues or []):
        if is_teacher(cue["speaker"], extra_teacher_names):
            continue
        m, score, amb = match(cue["speaker"], message_texts=[])
        if m and score >= 0.5 and not amb:
            _add(m, cue["speaker"], "observed")
    return alias_map

def build_mention_indexes(alias_map):
    """
    Từ alias_map (xem collect_known_aliases), dựng 3 tầng index để dò tên bị
    GV gọi trong lời nói — mỗi học sinh có thể được nhận diện qua NHIỀU alias
    khác nhau (tên thật + nickname Zoom), không chỉ 1 tên cố định:
    - full_index : chuẩn hoá TOÀN BỘ 1 alias (tên thật hoặc nickname) -> list
      entry (đủ tin cậy nhất, áp dụng cho mọi alias).
    - last2_index: 2 âm tiết CUỐI của 1 alias ≥2 âm tiết -> list entry (cách
      gọi tắt phổ biến với tên thật, vd "Quang Minh" từ "Nguyễn Văn Bình";
      áp dụng cho mọi alias).
    - token_index: TỪNG TỪ ĐƠN LẺ tách ra từ 1 nickname QUAN SÁT ĐƯỢC trong
      buổi (source="observed", vd "Phúc" tách từ nickname "Khoa Vũ") -> list
      entry. CỐ Ý KHÔNG áp dụng cho tên thật (source="real_name") — nếu tách
      tên thật ra từng âm tiết đơn lẻ sẽ rất dễ trùng (vd "Minh", "Nguyễn" lặp
      lại ở nhiều học sinh), còn nickname Zoom thực tế quan sát được thường
      đặc trưng hơn nên an toàn hơn để tách từ.
    """
    full_index = defaultdict(list)
    last2_index = defaultdict(list)
    token_index = defaultdict(list)
    for info in alias_map.values():
        e = info["entry"]
        seen_full, seen_last2, seen_tok = set(), set(), set()
        for alias, source in info["aliases"].items():
            an = norm(strip_stt(alias))
            if not an or an in seen_full:
                continue
            seen_full.add(an)
            full_index[an].append(e)
            toks = an.split()
            if len(toks) >= 2:
                l2 = " ".join(toks[-2:])
                if l2 not in seen_last2:
                    seen_last2.add(l2)
                    last2_index[l2].append(e)
            if source == "observed":
                for tok in toks:
                    if len(tok) >= 2 and tok not in seen_tok:
                        seen_tok.add(tok)
                        token_index[tok].append(e)
    return full_index, last2_index, token_index

def _contains_phrase(phrase, text_norm):
    """True nếu `phrase` (đã chuẩn hoá) xuất hiện trong `text_norm` với RANH
    GIỚI TỪ rõ ràng ở cả 2 đầu — KHÔNG tính là khớp nếu phrase chỉ là 1 đoạn
    ký tự con nằm giữa 1 từ khác (rà soát độ chính xác phát hiện: bản trước
    dùng substring thường `phrase in text_norm`, có thể khớp NHẦM — vd alias
    ngắn "an" vô tình khớp vào bên trong "ngoan"/"toan" sau khi bỏ dấu, hoặc
    last2 "an khoi" vô tình khớp vào chuỗi "toan khoing" dù 2 từ đó không
    liên quan gì đến cái tên đang tìm)."""
    if not phrase:
        return False
    return re.search(rf"(?:^|\s){re.escape(phrase)}(?:$|\s)", text_norm) is not None

def find_teacher_name_calls(cues, alias_map, extra_teacher_names=None):
    """
    Dò trong lời nói của GV/TG (is_teacher — DÙNG CHUNG cơ chế loại GV đang có,
    không viết riêng) xem có nhắc TÊN (thật HOẶC nickname Zoom đã biết — xem
    collect_known_aliases) của 1 học sinh hay không.

    3 mức bằng chứng, CHỈ chấp nhận khi khớp KHÔNG mơ hồ VÀ có RANH GIỚI TỪ rõ
    ràng (KHÔNG đoán thấp hơn — nguyên tắc chung của skill là thà bỏ sót còn
    hơn đoán bừa):
    - "full" : toàn bộ 1 alias (tên thật hoặc nickname) xuất hiện liền nhau.
    - "last2": 2 âm tiết cuối của 1 alias ≥2 âm tiết, CHỈ nhận nếu không trùng
      alias của học sinh khác.
    - "token" : 1 TỪ ĐƠN LẺ tách ra từ 1 nickname Zoom quan sát được (không áp
      dụng cho tên thật, xem build_mention_indexes), CHỈ nhận nếu từ đó không
      trùng với alias/nickname của học sinh khác.

    Nếu tên/nickname bị gọi trùng giữa ≥2 học sinh khác tên (không giải quyết
    được), trả về entry=None kèm danh sách tên gây trùng, để trợ giảng tự nghe lại.

    Trả về list dict: {start, speaker, content, entry, ambiguous}.
    """
    full_index, last2_index, token_index = build_mention_indexes(alias_map)

    calls = []
    for cue in cues:
        if not is_teacher(cue["speaker"], extra_teacher_names):
            continue
        content_norm = norm(cue["content"])
        if not content_norm:
            continue
        matched_entry_keys = set()  # tránh báo trùng nhiều lần cho cùng 1 học sinh trong cùng 1 cue
        for fn, entries in full_index.items():
            if not _contains_phrase(fn, content_norm):
                continue
            names = sorted({e["name"] for e in entries})
            if len(names) == 1 and len(entries) == 1:
                if entry_key(entries[0]) in matched_entry_keys:
                    continue
                matched_entry_keys.add(entry_key(entries[0]))
                calls.append({"start": cue["start"], "speaker": cue["speaker"],
                              "content": cue["content"], "entry": entries[0], "ambiguous": None})
            else:
                calls.append({"start": cue["start"], "speaker": cue["speaker"],
                              "content": cue["content"], "entry": None, "ambiguous": names})
        for l2, entries in last2_index.items():
            if not _contains_phrase(l2, content_norm):
                continue
            names = sorted({e["name"] for e in entries})
            if len(names) == 1 and len(entries) == 1:
                if entry_key(entries[0]) in matched_entry_keys:
                    continue
                matched_entry_keys.add(entry_key(entries[0]))
                calls.append({"start": cue["start"], "speaker": cue["speaker"],
                              "content": cue["content"], "entry": entries[0], "ambiguous": None})
            elif len(names) > 1:
                calls.append({"start": cue["start"], "speaker": cue["speaker"],
                              "content": cue["content"], "entry": None, "ambiguous": names})
        for token, entries in token_index.items():
            # chỉ khớp khi token đứng RIÊNG (có ranh giới từ), tránh khớp vào
            # giữa 1 từ khác chứa cùng chuỗi ký tự con
            if not _contains_phrase(token, content_norm):
                continue
            names = sorted({e["name"] for e in entries})
            if len(names) == 1 and len(entries) == 1:
                if entry_key(entries[0]) in matched_entry_keys:
                    continue
                matched_entry_keys.add(entry_key(entries[0]))
                calls.append({"start": cue["start"], "speaker": cue["speaker"],
                              "content": cue["content"], "entry": entries[0], "ambiguous": None})
            elif len(names) > 1:
                calls.append({"start": cue["start"], "speaker": cue["speaker"],
                              "content": cue["content"], "entry": None, "ambiguous": names})
    return calls

def load_chat_timestamped(chat_txt, extra_teacher_names=None):
    """Đọc lại (các) file chat với ĐÚNG cơ chế gộp/loại trùng như load_chat()
    (dùng chung `_cluster_chat_entries`), nhưng GIỮ LẠI mốc thời gian thực
    (wall-clock) của từng tin — load_chat() bỏ mốc thời gian sau khi gộp nên
    không tái dùng được cho việc đối chiếu thời gian với .vtt.

    QUAN TRỌNG: bản trước tự viết lại 1 logic loại trùng RIÊNG theo mốc giây Y
    HỆT, dính ĐÚNG lỗi đếm đúp đã tìm thấy và sửa ở merge_chat_entries() (lệch
    1-2 giây giữa 2 file export của cùng 1 buổi khiến 1 tin bị tính thành 2) —
    chưa từng lộ ra thành sai số hiển thị vì hàm này hiện chỉ phục vụ tính
    năng "gọi tên không trả lời" (đang tắt mặc định) dùng kết quả kiểu tồn-
    tại-hay-không (has any message in window), nên 1 tin bị đếm đúp không đổi
    kết luận cuối, nhưng vẫn sửa để nhất quán — nếu sau này có chỗ khác dùng
    lại hàm này theo kiểu ĐẾM số lượng thay vì chỉ kiểm tra có/không thì không
    bị dính lại lỗi cũ. Nay dùng chung `_cluster_chat_entries()` với
    merge_chat_entries() thay vì viết logic loại trùng riêng lần 2 (tránh lặp
    lại đúng kiểu lỗi từng mất `restrict_keys` khi sửa/viết trùng lặp 1 chỗ
    logic ở 2 nơi khác nhau).

    Trả về list (dt: datetime, sender: str, text: str) đã sắp theo thời gian.
    """
    paths = [chat_txt] if isinstance(chat_txt, str) else list(chat_txt)
    all_raw = []
    for path in paths:
        entries, _ = _parse_chat_file(path)
        all_raw.extend(entries)
    clustered = _cluster_chat_entries(all_raw)
    out = []
    for dt, sender, _receiver, msg_lines in clustered:
        content = "\n".join(l.strip() for l in msg_lines).strip()
        if is_teacher(sender, extra_teacher_names) or not content:
            continue
        out.append((dt, sender, content))
    return out

def detect_calls_without_response(cues, alias_map, match, chat_ts_entries,
                                   extra_teacher_names=None, mic_excuse_names=None,
                                   xin_nghi_buoi_nay=None, window_seconds=300):
    """
    Tạo danh sách CÁC CA NGHI VẤN 'GV gọi tên bằng giọng nói trong .vtt nhưng
    học sinh không phản hồi' — CHỈ để trợ giảng tự xác nhận, KHÔNG tự động
    trừ điểm (xem SKILL.md). Lý do KHÔNG tự trừ: đây là 3 tầng suy đoán chồng
    lên nhau (khớp tên bằng giọng nói dịch qua transcript tự động, cửa sổ thời
    gian phản hồi ước lượng, mốc neo thời gian giữa .vtt và chat chỉ là gần
    đúng) — rủi ro sai đủ cao để bắt buộc có người xác nhận trước khi ảnh
    hưởng điểm thật của học sinh.

    alias_map: xem collect_known_aliases() — cho phép nhận diện GV gọi tên
    theo TÊN THẬT hoặc NICKNAME ZOOM đã biết chắc chắn của học sinh trong
    chính buổi này (GV thường gọi theo tên hiển thị Zoom chứ không phải tên
    thật trong danh sách lớp).

    Một ca CHỈ bị liệt kê nếu ĐỒNG THỜI đúng cả 3:
    1. Không có lượt phát biểu nào của CHÍNH học sinh đó (khớp bằng `match`,
       cơ chế DÙNG CHUNG với chat/.vtt, không viết riêng) trong .vtt, trong
       khoảng [t_gọi, t_gọi + window_seconds].
    2. Không có tin nhắn chat nào của học sinh đó trong CÙNG khoảng thời gian,
       quy đổi sang giờ thực bằng ANCHOR = mốc thời gian tin nhắn chat SỚM
       NHẤT trong buổi (coi như trùng giây 0 của .vtt) — ĐÂY LÀ GIẢ ĐỊNH GẦN
       ĐÚNG, vì Zoom cloud recording có thể bắt đầu ghi trễ hơn tin nhắn đầu
       vài chục giây, không có mốc neo tuyệt đối chính xác. Vì vậy mọi ca ở
       đây LUÔN cần trợ giảng xác nhận lại, không dùng số liệu này để trừ tự động.
    3. Không có lý do xin phép không mở mic: tên nằm trong `mic_excuse_names`
       (trợ giảng tự trích từ Zalo, cùng cách làm với `xin_nghi_buoi_nay` nhưng
       cho lý do mic), HOẶC chính học sinh đó có ít nhất 1 tin nhắn (bất cứ lúc
       nào trong buổi, trên Zoom chat) chứa từ khoá lỗi mic (MIC_EXCUSE_KEYWORDS_NORM).

    Học sinh đã có trong `xin_nghi_buoi_nay` (vắng có phép cả buổi) được loại
    khỏi danh sách nghi vấn để tránh phạt chồng lên nhánh vắng có phép.

    Trả về list dict theo đúng thứ tự thời gian gọi tên, mỗi dict có:
    {entry, ambiguous_names, start, content, reason}.
    """
    if not cues:
        return []
    excuse_names_norm = {norm(n) for n in (mic_excuse_names or [])}
    absent_names_norm = {norm(n) for n in (xin_nghi_buoi_nay or [])}

    msgs_by_key = defaultdict(list)  # entry_key -> list (dt, text)
    for dt, sender, text in chat_ts_entries:
        m, score, _amb = match(sender, message_texts=[text])
        if m and score >= 0.5:
            msgs_by_key[entry_key(m)].append((dt, text))

    anchor_dt = min(dt for dt, _s, _t in chat_ts_entries) if chat_ts_entries else None

    speak_cues_by_key = defaultdict(list)  # entry_key -> list start_seconds
    for cue in cues:
        if is_teacher(cue["speaker"], extra_teacher_names):
            continue
        m, score, _amb = match(cue["speaker"], message_texts=[])
        if m and score >= 0.5:
            speak_cues_by_key[entry_key(m)].append(cue["start"])

    calls = find_teacher_name_calls(cues, alias_map, extra_teacher_names)

    results = []
    seen_pairs = set()
    for call in calls:
        e = call["entry"]
        if e is None:
            results.append({"entry": None, "ambiguous_names": call["ambiguous"],
                             "start": call["start"], "content": call["content"],
                             "reason": "Tên bị gọi trùng giữa nhiều học sinh, không xác định được là ai — "
                                       "cần bạn tự nghe lại đoạn ghi hình."})
            continue
        if norm(e["name"]) in absent_names_norm:
            continue
        key = entry_key(e)
        dedupe_key = (key, round(call["start"] / 30))
        if dedupe_key in seen_pairs:
            continue
        t0 = call["start"]
        window_end = t0 + window_seconds
        if any(t0 <= s <= window_end for s in speak_cues_by_key.get(key, [])):
            continue  # đã trả lời bằng giọng nói trong cửa sổ thời gian
        answered_chat = False
        if anchor_dt is not None:
            win_start = anchor_dt.timestamp() + t0
            win_end = anchor_dt.timestamp() + window_end
            for dt, _text in msgs_by_key.get(key, []):
                if win_start <= dt.timestamp() <= win_end:
                    answered_chat = True
                    break
        if answered_chat:
            continue
        has_excuse = norm(e["name"]) in excuse_names_norm or any(
            _has_mic_excuse(text) for _dt, text in msgs_by_key.get(key, []))
        if has_excuse:
            continue
        seen_pairs.add(dedupe_key)
        results.append({"entry": e, "ambiguous_names": None, "start": t0, "content": call["content"],
                         "reason": f"Không thấy lượt phát biểu/tin nhắn nào trong ~{window_seconds // 60} phút "
                                   f"sau khi bị gọi tên, và không thấy lý do xin phép không mở mic."})
    return results

def _resolve_names_to_roster_entries(names, roster):
    """Khớp danh sách tên (hoặc 'STT. Tên' để phân biệt trùng tên) TRỰC TIẾP với
    roster — KHÔNG phụ thuộc việc học sinh đó đã có tin nhắn/lượt phát biểu nào
    trong buổi hay chưa. Dùng cho manual_checkin_names: cho phép ép điểm danh cả
    học sinh hoàn toàn im lặng (0 tin, 0 lượt nói) — trước đây chỉ tìm trong
    student_entry (chỉ chứa người ĐÃ xuất hiện trong dữ liệu buổi học) nên với
    học sinh 0 tin/0 lượt nói thì không có entry nào để gắn cờ lên, khiến việc
    ép điểm danh ÂM THẦM không có tác dụng (không báo lỗi gì, báo cáo vẫn ghi
    vắng không phép như cũ). Trả về list các roster entry khớp được (nếu tên bị
    trùng và không có STT phân biệt, trả về TẤT CẢ các entry cùng tên đó — an
    toàn hơn là bỏ sót, giống cách _resolve_penalty_targets xử lý)."""
    targets = []
    for raw in (names or []):
        raw = str(raw).strip()
        stt_hint = extract_leading_stt(raw)
        name_norm = norm(strip_stt(raw))
        candidates = [r for r in roster if norm(r["name"]) == name_norm]
        if stt_hint is not None:
            with_stt = [r for r in candidates if stt_matches(r, stt_hint)]
            if with_stt:
                candidates = with_stt
        targets.extend(candidates)
    return targets

def _resolve_penalty_targets(penalty_names, student_entry):
    """Khớp danh sách tên (hoặc 'STT. Tên' để phân biệt trùng tên) do trợ giảng
    XÁC NHẬN THỦ CÔNG cần trừ điểm, với student_entry hiện có trong bảng xếp
    hạng. Trả về set các entry_key cần trừ."""
    targets = set()
    for raw in (penalty_names or []):
        raw = str(raw).strip()
        stt_hint = extract_leading_stt(raw)
        name_norm = norm(strip_stt(raw))
        candidates = [k for k, e in student_entry.items() if norm(e["name"]) == name_norm]
        if stt_hint is not None:
            with_stt = [k for k in candidates if stt_matches(student_entry[k], stt_hint)]
            if with_stt:
                candidates = with_stt
        targets.update(candidates)
    return targets

# ======================= PHẦN A: BẢNG XẾP HẠNG =======================
LATE_ARRIVAL_MARKERS_NORM = ["vao muon", "den muon", "vao tre", "xin phep vao", "xin vao muon"]

# Từ khoá cho biết RÕ RÀNG đây là 1 câu điểm danh dù có thêm chữ khác quanh tên
# (vd nhắc TG/GV, thêm lời chào...). Ca thực tế: "em Bùi Cường điểm danh anh
# Đô nhé" (8 từ, tên chỉ có 2 từ "Bùi Cường") từng bị bỏ sót và bị tính vắng
# không phép, dù đây rõ ràng là 1 tin điểm danh — thêm marker này để không lặp
# lại ca tương tự trong tương lai (không cần chờ trợ giảng xác nhận thủ công).
CHECKIN_KEYWORD_MARKERS_NORM = ["diem danh", "xin diem danh", "co mat a", "co mat day"]

def looks_like_checkin(text, name):
    t, n = norm(text), norm(name)
    ttoks, ntoks = set(t.split()), set(n.split())
    if not ttoks or not ntoks:
        return False
    overlap = ntoks & ttoks
    covers_name = len(overlap) / len(ntoks) >= 0.6
    if not covers_name:
        return False
    not_too_long = len(ttoks) <= len(ntoks) + 4
    if not_too_long:
        return True
    # Câu tự báo "vào muộn" thường dài hơn 1 câu điểm danh thông thường
    # (vd "Kim Tùng Lâm xin phép vào muộn ạ" = tên + 4 từ, tổng > ngưỡng cứng
    # phía trên) — vẫn tính là ĐÃ ĐIỂM DANH (có mặt, vào muộn) nếu chứa cụm từ
    # báo muộn rõ ràng, thay vì bị bỏ sót chỉ vì câu hơi dài hơn 1-2 từ.
    if any(marker in t for marker in LATE_ARRIVAL_MARKERS_NORM) and len(ttoks) <= len(ntoks) + 8:
        return True
    # Câu chứa rõ từ khoá "điểm danh" (hoặc tương đương) đi kèm tên đầy đủ của
    # chính học sinh đó -> vẫn coi là ĐÃ ĐIỂM DANH dù câu dài hơn ngưỡng thường,
    # miễn không dài quá mức (tránh khớp bừa vào câu chuyện dài chỉ tình cờ có
    # nhắc "điểm danh" ở đâu đó không liên quan đến chính người gửi).
    if any(marker in t for marker in CHECKIN_KEYWORD_MARKERS_NORM) and len(ttoks) <= len(ntoks) + 8:
        return True
    return False

def _find_exact_name_declaration(texts_list, roster, exclude_entry=None):
    """
    Tìm 1 tin nhắn TRÙNG Y HỆT (sau khi bỏ số thứ tự đầu dòng + chuẩn hoá dấu)
    với TÊN ĐẦY ĐỦ của 1 học sinh KHÁC (khác exclude_entry) trong roster —
    đây là bằng chứng RẤT MẠNH (không phải chỉ trùng vài âm tiết như các ca
    nhầm lẫn thường gặp) rằng tài khoản Zoom đang xét, dù tên hiển thị trùng
    với người khác, THỰC RA thuộc về học sinh này.

    Ca thực tế: 2 tài khoản Zoom CÙNG hiển thị "Ngô Thanh Sơn" tham gia đồng thời
    trong 1 buổi — 1 tài khoản tự giới thiệu "Ngô thanh Sơn" (khớp đúng tên hiển
    thị, giữ nguyên), tài khoản còn lại tự giới thiệu "Cao Bá Thịnh" (tên
    ĐẦY ĐỦ của 1 học sinh HOÀN TOÀN khác trong danh sách) — rõ ràng đây là 2
    người khác nhau dùng chung 1 tên hiển thị Zoom, KHÔNG được gộp làm 1.

    Chỉ chấp nhận khi cả câu (không phải 1 phần câu) khớp y hệt 1 tên roster
    khác, để tránh nhầm với việc nhắc/tag tên người khác giữa 1 câu dài hơn.

    Bug thực tế từng gặp: hàm bỏ STT trong tin nhắn TRƯỚC khi so khớp, nên khi
    roster có ≥2 học sinh TRÙNG Y HỆT họ tên (vd "Lê Văn Bình" ở STT 65 và
    97), 1 học sinh tự xác nhận ĐÚNG danh tính của CHÍNH MÌNH (Zoom hiển thị
    "97. Lê Văn Bình", tự nhắn lại đúng "97. Lê Văn Bình") bị hiểu
    nhầm thành "tự giới thiệu sang người khác" — vì sau khi bỏ số "97." thì
    câu chỉ còn "Lê Văn Bình", trùng tên với CẢ exclude_entry (STT97, đúng)
    LẪN người kia (STT65, sai) như nhau, và hàm trả về người đầu tiên tìm thấy
    trong roster (không phải exclude_entry) — tức trả nhầm STT 65.

    Sửa: KHÔNG chỉ nhìn tên suông — nếu nội dung (sau khi bỏ STT) trùng Y HỆT
    với TÊN CỦA CHÍNH exclude_entry (tức nghi ngờ rơi vào đúng ca lỗi trên,
    exclude_entry trùng tên với ai đó khác trong roster), CHỈ coi là "chuyển
    sang người khác" khi tin nhắn có kèm 1 STT rõ ràng và STT đó KHÁC STT của
    exclude_entry — không có STT hoặc STT trùng đúng STT của exclude_entry thì
    coi là tự xác nhận lại CHÍNH MÌNH, bỏ qua (an toàn hơn đoán bừa). Ngược lại
    — nếu nội dung khớp tên 1 người KHÁC HẲN tên exclude_entry (trường hợp phổ
    biến nhất, vd ca "Ngô Thanh Sơn"/"Cao Bá Thịnh" ở trên) — giữ hành vi gốc,
    không cần đòi hỏi STT, vì không hề có sự mập mờ "tự xác nhận chính mình"
    kiểu trên (tên hoàn toàn khác thì không thể là chính exclude_entry được).
    """
    exclude_name_norm = norm(exclude_entry["name"]) if exclude_entry is not None else None
    for t in texts_list:
        cleaned = norm(strip_stt(t))
        if not cleaned:
            continue
        stt_in_text = extract_leading_stt(t)

        if exclude_name_norm is not None and cleaned == exclude_name_norm:
            # Nội dung trùng Y HỆT tên của CHÍNH exclude_entry -> rơi đúng vào
            # ca có thể trùng tên với người khác trong roster. Không có STT
            # hoặc STT khớp đúng exclude_entry -> tự xác nhận chính mình.
            if stt_in_text is None or (exclude_entry is not None and stt_matches(exclude_entry, stt_in_text)):
                continue
            candidates = [r for r in roster
                          if r is not exclude_entry and cleaned == norm(r["name"])]
            pick = pick_by_stt(candidates, stt_in_text)
            if pick is not None:
                return pick
            continue  # có STT nhưng không khớp ai trong số các ứng viên trùng tên -> bỏ qua

        candidates = [r for r in roster
                      if (exclude_entry is None or r is not exclude_entry)
                      and cleaned == norm(r["name"])]
        if not candidates:
            continue
        if len(candidates) == 1:
            return candidates[0]
        if stt_in_text is not None:
            pick = pick_by_stt(candidates, stt_in_text)
            if pick is not None:
                return pick
        # Nhiều người trùng tên và không có STT nào trong tin nhắn để phân
        # biệt được ai trong số họ -> không đủ căn cứ, bỏ qua tin này.
    return None

def build_ranking_table(roster, match, counts, texts, speak_counts=None, manual_checkin_names=None,
                         restrict_keys=None, penalty_names=None, penalty_amount=5, roster_full=None):
    """
    roster_full : TOÀN BỘ roster của lớp (không lọc theo filter_code) — dùng riêng
    cho việc PHÁT HIỆN trùng tên (find_duplicate_names) và tách 2 tài khoản Zoom
    dùng chung tên hiển thị (_find_exact_name_declaration). 2 việc này phải luôn
    xét trên CẢ LỚP dù đang tạo báo cáo lọc riêng 1 nhóm (filter_code), vì bản
    chất trùng tên/nhầm tài khoản là vấn đề của TOÀN LỚP (match() cũng luôn khớp
    trên roster_full) — nếu chỉ xét trong roster đã lọc, có thể BỎ SÓT 1 ca trùng
    tên (khi chỉ 1 trong 2 người trùng tên thuộc nhóm đang lọc) hoặc không tách
    được 2 tài khoản khi người tự giới thiệu lại thuộc nhóm KHÁC ngoài phạm vi
    đang lọc. Mặc định = roster (giữ nguyên hành vi cũ khi không có filter_code,
    vì lúc đó roster và roster_full vốn là cùng 1 danh sách).

    penalty_names : list tên (hoặc "STT. Tên" nếu trùng tên) đã được trợ giảng
    XÁC NHẬN THỦ CÔNG (sau khi tự xem lại danh sách nghi vấn "GV gọi tên
    nhưng không trả lời" — xem detect_calls_without_response) — mỗi tên trong
    đây bị trừ `penalty_amount` (mặc định 5) khỏi tổng "Số tin", không cho
    xuống dưới 0. KHÔNG tự động áp dụng nếu không được truyền vào tường minh —
    đây luôn là hành động 2 bước (xem nghi vấn trước, xác nhận trừ sau).

    restrict_keys: None (mặc định) = không lọc, hiện mọi người có chat (dùng cho báo
    cáo tổng). Nếu được truyền vào (khi generate_report có filter_code, vd lọc riêng
    nhóm "3T") thì đây là set các entry_key(entry) thuộc nhóm đang lọc — CHỈ những
    học sinh có key nằm trong set này mới được đưa vào bảng xếp hạng Phần A; học sinh
    khớp roster nhưng KHÔNG thuộc nhóm bị loại hẳn khỏi bảng, và người chưa khớp được
    (unmatched, kể cả người chỉ phát biểu qua .vtt) cũng bị loại (vì không xác định
    được có thuộc nhóm hay không) — tránh lặp lại lỗi cũ là Phần A không áp dụng
    filter_code.

    speak_counts : dict {tên người nói gốc trong file .vtt: số lượt phát biểu}
    (tuỳ chọn, xem load_vtt_transcript — Vấn đề 3). Số lượt phát biểu được
    CỘNG THẲNG vào tổng "Số tin" hiện có của mỗi học sinh (để không đổi ngưỡng
    tương tác tốt/kém đang dùng), đồng thời lưu riêng để hiển thị thêm 1 cột
    "Số lượt phát biểu" — nội dung lời nói KHÔNG được gộp vào cột nội dung
    chat (2 loại nội dung khác bản chất). Khớp tên người nói dùng ĐÚNG matcher
    `match` đang dùng cho chat, không có cơ chế khớp tên riêng thứ 2.
    """
    roster_full = roster if roster_full is None else roster_full
    dup_names = find_duplicate_names(roster_full)
    student_msgs = defaultdict(int)      # key = entry_key(entry)
    student_texts = defaultdict(list)    # key = entry_key(entry)
    student_entry = {}                   # entry_key(entry) -> entry (để lấy tên/STT khi hiển thị)
    student_checkin = defaultdict(bool)  # key = entry_key(entry)
    student_speak = defaultdict(int)     # key = entry_key(entry) -> số lượt phát biểu (Vấn đề 3)
    unmatched = []
    speak_unmatched = []  # (speaker, cnt) — người nói trong .vtt không khớp chắc chắn với roster
    # LƯU Ý: trước đây có thêm 1 list `ambiguous` riêng để cảnh báo "đã tạm gán
    # X cho STT Y" khi tên hoà điểm giữa ≥2 người — đã bỏ, vì match() giờ luôn
    # trả entry=None khi ambiguous=True (không còn "tạm gán" cho ai nữa, xem
    # docstring match()) nên list đó luôn rỗng. Các ca này giờ tự động rơi vào
    # `unmatched`/`speak_unmatched` và dùng chung cảnh báo "chưa khớp chắc
    # chắn" — không mất thông tin, chỉ gộp chung 1 cơ chế cảnh báo thay vì 2.
    sender_resolution = {}  # raw sender chat -> (entry hoặc None, ambiguous bool) — để tái dùng khi đối chiếu participants.csv
    special_cases = []  # (raw_sender, entry_theo_ten_hien_thi, entry_theo_tu_gioi_thieu) — 2 tài khoản
                         # Zoom trùng tên hiển thị nhưng nội dung tự giới thiệu chỉ rõ là 2 người khác nhau

    for sender, cnt in counts.items():
        m, score, is_ambiguous = match(sender, message_texts=texts[sender])
        if m is not None and score >= 0.5:
            declared = _find_exact_name_declaration(texts[sender], roster_full, exclude_entry=m)
            if declared is not None:
                special_cases.append((sender, m, declared))
                m = declared  # tin tự giới thiệu (bằng chứng mạnh hơn tên hiển thị Zoom) được ưu tiên
        sender_resolution[sender] = (m if (m and score >= 0.5) else None, is_ambiguous)
        if m and score >= 0.5:
            key = entry_key(m)
            if restrict_keys is not None and key not in restrict_keys:
                continue  # không thuộc nhóm đang lọc (vd không mang mã filter_code) -> bỏ khỏi Phần A
            student_msgs[key] += cnt
            student_texts[key].extend(texts[sender])
            student_entry[key] = m
            if any(looks_like_checkin(t, m["name"]) for t in texts[sender]):
                student_checkin[key] = True
        else:
            if restrict_keys is not None:
                continue  # đang lọc riêng 1 nhóm -> không rõ người chưa khớp có thuộc nhóm không, bỏ qua
            unmatched.append((sender, cnt, texts[sender]))

    for speaker, cnt in (speak_counts or {}).items():
        m, score, is_ambiguous = match(speaker, message_texts=[])
        if not (m and score >= 0.5):
            # Cùng tên hiển thị Zoom với 1 sender chat đã khớp chắc chắn (qua tự giới thiệu) -> cùng tài khoản
            _res = sender_resolution.get(speaker)
            if _res and _res[0] is not None:
                m, score = _res[0], 1.0
        if m and score >= 0.5:
            key = entry_key(m)
            if restrict_keys is not None and key not in restrict_keys:
                continue  # đồng bộ với chat: lượt phát biểu ngoài nhóm đang lọc cũng bị loại khỏi Phần A
            student_msgs[key] += cnt  # cộng vào tổng "Số tin", không đổi ngưỡng tương tác
            student_speak[key] += cnt
            student_entry.setdefault(key, m)
        else:
            if restrict_keys is not None:
                continue
            speak_unmatched.append((speaker, cnt))

    if manual_checkin_names:
        for entry in _resolve_names_to_roster_entries(manual_checkin_names, roster):
            key = entry_key(entry)
            student_checkin[key] = True
            student_entry.setdefault(key, entry)

    penalized_keys = set()
    if penalty_names:
        penalized_keys = _resolve_penalty_targets(penalty_names, student_entry)
        for key in penalized_keys:
            student_msgs[key] = max(0, student_msgs[key] - penalty_amount)

    rows = sorted(student_msgs.items(), key=lambda kv: -kv[1])
    combined = [(key, cnt, False) for key, cnt in rows]
    combined += [(f"⚠️ {sender}", cnt, True) for sender, cnt, _ in unmatched]
    combined += [(f"⚠️ [thoại] {speaker}", cnt, True) for speaker, cnt in speak_unmatched]
    combined = sorted(combined, key=lambda x: -x[1])

    has_speech = bool(speak_counts)
    if has_speech:
        lines = ["| Rank | STT. Họ và tên | Số tin | Số lượt phát biểu | Nội dung chat |",
                 "|-----:|----------------|:------:|:------:|---------------|"]
    else:
        lines = ["| Rank | STT. Họ và tên | Số tin | Nội dung chat |",
                 "|-----:|----------------|:------:|---------------|"]
    rank, prev_count = 0, None
    for idx, (key, cnt, is_unmatched) in enumerate(combined, start=1):
        if cnt != prev_count:
            rank, prev_count = idx, cnt
        speak_col = 0
        if is_unmatched:
            display_name = key  # đã có prefix "⚠️ " + tên sender gốc (hoặc "[thoại] tên")
            raw_key = key.replace("⚠️ ", "")
            if raw_key.startswith("[thoại] "):
                content_texts = []  # không có nội dung chat cho lượt phát biểu chưa khớp
                speak_col = cnt
            else:
                content_texts = next(t for s, c, t in unmatched if s == raw_key)
        else:
            entry = student_entry[key]
            base_name = entry["name"]
            stt = display_num(entry, dup_names)
            display_name = f"{stt}. {base_name}" if stt is not None else base_name
            if base_name in dup_names:
                display_name += " ⚠️(trùng tên, đã tách theo STT)"
            if key in penalized_keys:
                display_name += f" (-{penalty_amount} do gọi tên không trả lời, đã xác nhận)"
            content_texts = student_texts[key]
            speak_col = student_speak.get(key, 0)
        # Chỉ thêm "..." khi THỰC SỰ có nội dung bị cắt bớt. `cnt` là tổng "Số
        # tin" (chat + lượt phát biểu qua .vtt cộng dồn), còn content_texts chỉ
        # chứa tin CHAT thật — 1 học sinh có thể ít tin chat nhưng nói nhiều qua
        # mic khiến cnt > FULL_MSG_LIMIT dù content_texts chưa tới TRUNCATE_SHOW
        # dòng; trước đây vẫn tự thêm "..." trong TH đó dù đã hiện đủ 100% tin
        # chat, gây hiểu lầm là còn nội dung bị ẩn.
        if cnt <= FULL_MSG_LIMIT:
            content = ", ".join(content_texts)
        else:
            shown = content_texts[:TRUNCATE_SHOW]
            content = ", ".join(shown)
            if len(content_texts) > len(shown):
                content += "..."
        content = content.replace("|", "/").replace("\n", " ")
        if has_speech:
            lines.append(f"| {rank} | {display_name} | {cnt} | {speak_col} | {content} |")
        else:
            lines.append(f"| {rank} | {display_name} | {cnt} | {content} |")

    return ("\n".join(lines), student_msgs, student_checkin, unmatched, student_entry,
            sender_resolution, student_speak, speak_unmatched, special_cases)

# ======================= PHẦN B: BÁO CÁO TỔNG QUAN =======================
# Mẫu Phần B RIÊNG cho 1 số nhóm con (filter_code) — khác cách xưng hô/emoji/
# nhãn dòng so với mẫu mặc định gửi cả lớp, theo đúng mẫu người dùng cung cấp
# (nhóm 3T gửi cho phụ huynh nên xưng hô khác, không có hậu tố "(chỉ nhóm 3T)"
# ở dòng đầu). Nếu sau này có nhóm khác cần mẫu riêng, thêm entry mới ở đây —
# không cần sửa lại hàm build_summary. Khoá dict là filter_code đã CHUẨN HOÁ
# (norm()) — GIỐNG HỆT cách filter_roster_by_code so khớp mã nhóm với roster
# (không phân biệt hoa/thường/dấu) — để tránh 1 lỗi ngầm: nếu tra thẳng bằng
# filter_code chưa chuẩn hoá, gọi generate_report(filter_code="3t") (chữ
# thường) vẫn lọc đúng roster nhóm 3T (vì filter_roster_by_code() đã chuẩn hoá)
# nhưng lại ÂM THẦM rơi về mẫu mặc định thay vì mẫu 3T — không báo lỗi gì, chỉ
# lặng lẽ sai định dạng gửi phụ huynh.
GROUP_SUMMARY_TEMPLATES = {
    "3T": {
        "salutation": "Cháu/Anh gửi PH/các bạn",
        "emoji": "☘️",
        "vang_khong_phep_label": "vắng không phép",
        "vang_co_phep_label": "Vắng có phép",
        "title_suffix_override": "",
    },
}
GROUP_SUMMARY_TEMPLATES_NORM = {norm(k): v for k, v in GROUP_SUMMARY_TEMPLATES.items()}


def fuzzy_candidates_for_excused_name(raw_name, roster_norm):
    """Tìm ứng viên PHÙ HỢP cho 1 tên xin nghỉ KHÔNG khớp Y HỆT ai trong
    roster (chỉ gọi sau khi so khớp chính xác ở _resolve_excused_entries đã
    thất bại) — dùng cho tên bị THIẾU 1 TỪ (vd "Đinh Thùy Dung" thiếu "Thị" so
    với roster "Đinh Thị Thùy Dung") hoặc tên RÚT GỌN (vd chỉ gõ "Đỗ Hải").

    QUAN TRỌNG (phát hiện 27/09/2026) — KHÔNG dùng chung công thức chấm điểm
    đầy đủ với score_all() (dùng để khớp sender Zoom): score_all() có bonus
    "khớp chuỗi con LIÊN TIẾP" (+0.3, ưu tiên tên có thứ tự từ khớp NGUYÊN
    VĂN — hợp lý cho self-intro Zoom vì học sinh tự gõ tên MÌNH theo đúng thứ
    tự). Bonus đó GÂY HẠI ở đây: ca thực tế "Đỗ Hải" lẽ ra PHẢI mơ hồ giữa "Đỗ Minh Hải" (STT38, đáp án đúng, xác nhận qua ngữ cảnh ngoài văn bản) và
    "Đỗ Hải Long" (chỉ giống hơn về MẶT CHỮ) — nếu tính bonus chuỗi con liên
    tiếp, "Đỗ Hải Long" ("Đỗ Hải" nằm ngay đầu, liền mạch) được cộng điểm
    NHIỀU HƠN "Đỗ Minh Hải" ("Lê"..."Bảo" bị ngắt bởi "Hoàng" ở giữa) — 1.8
    so với 1.5 — dẫn tới CHỌN NHẦM một cách tự tin sang tên "trông giống hơn",
    dù thực tế đáp án đúng lại là tên kia. Vì tên xin nghỉ do trợ giảng gõ
    tay lại từ Zalo (KHÔNG phải học sinh tự gõ tên mình như self-intro Zoom),
    thứ tự chữ liền mạch KHÔNG đáng tin bằng ở đây — không được dùng làm căn
    cứ tự tin chọn 1 người.

    Nên CHỈ dùng đúng 1 tiêu chí: mọi từ (kể cả số lần lặp) của tên xin nghỉ
    có xuất hiện TRỌN VẸN trong tên roster hay không — `_submultiset_either_way`,
    CẢ 2 CHIỀU (chiều thiếu-từ: excused ⊆ roster; chiều dư-từ: roster ⊆
    excused, phòng khi tên xin nghỉ có thêm từ thừa chưa dọn hết, vd "Em Đinh Thùy Dung") — KHÔNG thưởng gì thêm cho thứ tự/liền mạch. 2 tên cùng chứa
    trọn "Lê"+"Bảo" thì cùng là ứng viên hợp lệ NGANG NHAU, phản ánh đúng
    việc thực sự mơ hồ, thay vì tự tin chọn nhầm 1 người.

    Trả về list các entry roster thoả điều kiện — KHÔNG có điểm số ưu tiên
    giữa các entry (cố tình, vì lý do trên): nơi gọi chỉ được tự resolve khi
    list này có ĐÚNG 1 phần tử."""
    n = norm(strip_stt(raw_name))
    tokens = Counter(n.split())
    if not tokens:
        return []
    return [entry for rn, entry in roster_norm
            if _submultiset_either_way(tokens, Counter(rn.split()))]

def _resolve_excused_entries(roster, xin_nghi, dup_names_norm):
    """Khớp danh sách xin nghỉ (mỗi phần tử "Tên" hoặc "STT. Tên") với đúng các
    entry roster. Trả về (set entry_key, list cảnh báo trùng tên, list
    unresolved cần hỏi lại — xem bên dưới).

    3 tầng, theo thứ tự tin cậy giảm dần:
    1. Khớp Y HỆT tên (đã chuẩn hoá) — như cũ:
       - Không trùng ai khác: nhận luôn.
       - Trùng ≥2 người + có số khớp đúng 1 người (STT hoặc số tên đăng nhập,
         xem pick_by_stt): chỉ áp dụng cho người đó.
       - Trùng ≥2 người, không có số / số không khớp ai: áp dụng cho TẤT CẢ
         người cùng tên (an toàn hơn bỏ sót MỘT NGƯỜI THẬT SỰ TRÙNG TÊN — 2
         học sinh có tên hệt nhau, rủi ro nhầm ở đây thấp hơn hẳn tầng 2 bên
         dưới) và đưa vào warnings để trợ giảng xác nhận.
    2. KHÔNG khớp y hệt ai (phát hiện 27/09/2026, trước đây ÂM THẦM BỎ QUA,
       không cảnh báo gì) — thử fuzzy_candidates_for_excused_name() (tên
       thiếu từ/rút gọn):
       - Đúng 1 ứng viên: nhận luôn.
       - ≥2 ứng viên + có số khớp đúng 1 người: chỉ áp dụng người đó.
       - ≥2 ứng viên, không số/số không khớp: KHÔNG tự gán cho AI CẢ — khác
         tầng 1 ở trên, đây là 2 HỌC SINH KHÁC TÊN NHAU chỉ trùng 1 PHẦN chữ
         (vd "An Khang" khớp cả "Ngô An Khang" lẫn "Trần An Khang" — 2 người
         hoàn toàn không liên quan), tự gán cho cả 2 nghĩa là chắc chắn ghi
         sai vắng-có-phép cho 1 người ĐANG CÓ MẶT — rủi ro cao hơn hẳn tầng 1,
         nên để hẳn vào `unresolved`, không cộng key nào, mặc định rơi vào
         "vắng không phép" (dễ thấy để trợ giảng/AI xác nhận) thay vì đoán.
       - 0 ứng viên: cũng vào `unresolved` — có thể do gõ tắt quá nhiều, tên
         đã đổi, hoặc là học sinh MỚI chưa có trong roster.
    Mỗi phần tử `unresolved` là dict {"raw", "candidates"} để nơi gọi (báo cáo
    cuối + SKILL.md hướng dẫn AI hỏi lại người dùng) có đủ ngữ cảnh, không chỉ
    1 câu cảnh báo trống không."""
    roster_norm = [(norm(r["name"]), r) for r in roster]
    keys, warnings, unresolved = set(), [], []
    for raw in xin_nghi:
        raw = str(raw).strip()
        if not raw:
            continue
        num = extract_leading_stt(raw)
        name_n = norm(strip_stt(raw))
        cands = [r for r in roster if norm(r["name"]) == name_n]
        if cands:
            if len(cands) > 1 and name_n in dup_names_norm:
                pick = pick_by_stt(cands, num) if num is not None else None
                if pick is not None:
                    keys.add(entry_key(pick))
                    continue
                warnings.append(raw)
            keys.update(entry_key(r) for r in cands)
            continue

        fuzzy = fuzzy_candidates_for_excused_name(raw, roster_norm)
        if len(fuzzy) == 1:
            keys.add(entry_key(fuzzy[0]))
            continue
        if len(fuzzy) > 1:
            pick = pick_by_stt(fuzzy, num) if num is not None else None
            if pick is not None:
                keys.add(entry_key(pick))
                continue
        unresolved.append({"raw": raw, "candidates": fuzzy})
    return keys, warnings, unresolved

def build_summary(roster, fixed_notes, xin_nghi_buoi_nay, session_date,
                   student_msgs, student_checkin, student_entry, good_threshold, poor_threshold,
                   title_suffix="", filter_code=None):
    """
    QUAN TRỌNG: mọi tập hợp bên dưới (active, present, absent, ...) đều được
    xây dựng theo entry_key(entry) — tức theo TỪNG HỌC SINH THỰC trong roster
    — chứ KHÔNG theo tên (string). Nếu dùng tên làm khóa, 2 học sinh trùng tên
    (vd 2 bạn "Lê Văn Bình" khác STT) sẽ bị gộp thành 1 dòng và tự động
    làm sĩ số/số liệu vắng bị thiếu mất 1 người — đây chính là lỗi đã xảy ra
    trước đây, giờ được xử lý triệt để ở đây.
    """
    dup_names = find_duplicate_names(roster)
    dup_names_norm = {norm(n) for n in dup_names}
    da_nghi_norm = {norm(n) for n in fixed_notes.get("da_nghi_han", [])}
    hoc_thu_norm = {norm(n) for n in fixed_notes.get("hoc_thu", [])}
    xin_nghi = set(xin_nghi_buoi_nay or [])
    xin_nghi_warnings = []

    try:
        dt = datetime.strptime(session_date, "%d/%m/%Y")
        weekday_vn = VN_WEEKDAY[dt.weekday()]
        for name, wd in fixed_notes.get("always_absent_on_weekday", {}).items():
            if norm(wd) == norm(weekday_vn):
                xin_nghi.add(name)
    except ValueError:
        pass

    # so sánh bằng TÊN ĐÃ CHUẨN HOÁ (norm), không phải chuỗi y hệt: tên gõ tay
    # trong cấu hình lớp/xin_nghi_buoi_nay chỉ cần lệch 1 khoảng trắng thừa
    # hoặc khác cách gõ dấu là so khớp chuỗi y hệt sẽ ÂM THẦM thất bại (không
    # báo lỗi gì), khiến 1 học sinh đã nghỉ hẳn/học thử bị tính nhầm vào sĩ số,
    # hoặc 1 ca xin nghỉ có phép bị xếp nhầm thành vắng không phép.
    # Mỗi phần tử của xin_nghi có thể là "Tên" hoặc "STT. Tên" (STT = số trong
    # cột STT HOẶC số trong tên đăng nhập). Với tên KHÔNG trùng trong roster:
    # chỉ cần tên. Với tên TRÙNG (vd 2 bạn Lê Văn Bình): PHẢI kèm số để chỉ
    # đúng người — xem _resolve_excused_entries. Trước đây so khớp thuần theo
    # tên nên xin nghỉ cho 1 bạn là cả 2 bạn trùng tên cùng bị xếp "vắng có phép".
    xin_nghi_entry_keys, xin_nghi_warnings, xin_nghi_unresolved = _resolve_excused_entries(roster, xin_nghi, dup_names_norm)

    active_entries = [r for r in roster if norm(r["name"]) not in da_nghi_norm and norm(r["name"]) not in hoc_thu_norm]
    active_keys = {entry_key(r) for r in active_entries}
    key_to_entry = {entry_key(r): r for r in active_entries}

    good_by_volume = {k for k, c in student_msgs.items() if c > good_threshold}
    present_keys = (set(student_checkin) | good_by_volume) & active_keys
    absent_keys = active_keys - present_keys

    def label(key):
        entry = key_to_entry.get(key) or student_entry.get(key)
        return display_label(entry, dup_names)

    vang_co_phep_keys = sorted((k for k in absent_keys if k in xin_nghi_entry_keys),
                                key=lambda k: label(k))
    vang_khong_phep_keys = sorted((k for k in absent_keys if k not in xin_nghi_entry_keys),
                                   key=lambda k: label(k))
    tot_keys = sorted((k for k in present_keys if student_msgs.get(k, 0) > good_threshold),
                       key=lambda k: label(k))
    kem_keys = sorted((k for k in present_keys if student_msgs.get(k, 0) < poor_threshold),
                       key=lambda k: label(k))

    vang_co_phep = [label(k) for k in vang_co_phep_keys]
    vang_khong_phep = [label(k) for k in vang_khong_phep_keys]
    tot = [label(k) for k in tot_keys]
    kem = [label(k) for k in kem_keys]

    tmpl = GROUP_SUMMARY_TEMPLATES_NORM.get(norm(filter_code)) if filter_code else None
    if tmpl:
        salutation = tmpl["salutation"]
        emoji = tmpl["emoji"]
        vkp_label = tmpl["vang_khong_phep_label"]
        vcp_label = tmpl["vang_co_phep_label"]
        suffix = tmpl.get("title_suffix_override", title_suffix)
    else:
        salutation = "Anh gửi các bạn"
        emoji = "🍀"
        vkp_label = "Vắng không phép"
        vcp_label = "Vắng có phép"
        suffix = title_suffix

    out = [f"@All {salutation} Báo cáo tổng quan buổi học ngày {session_date}{suffix}:",
           f"{emoji} Sĩ số: {len(present_keys)}/{len(active_keys)}.",
           f"- {vkp_label}: {', '.join(vang_khong_phep) if vang_khong_phep else '(không có)'}.",
           f"- {vcp_label}: {', '.join(vang_co_phep) if vang_co_phep else '(không có)'}.",
           "",
           f"{emoji} Các bạn tương tác tốt: {', '.join(tot) if tot else '(không có)'}.",
           f"{emoji} Các bạn tương tác kém: {', '.join(kem) if kem else '(không có)'}."]

    if xin_nghi_warnings:
        out.append("")
        out.append("⚠️ Lưu ý: tên " + ", ".join(sorted(set(xin_nghi_warnings))) +
                    " trong danh sách xin nghỉ hôm nay bị TRÙNG với >1 học sinh trong danh sách "
                    "lớp mà không chỉ rõ được là bạn nào (thiếu số thứ tự, hoặc số không khớp ai) — "
                    "đã tạm áp dụng cho tất cả các bạn cùng tên, vui lòng xác nhận lại đúng là bạn nào "
                    "(truyền dạng \"STT. Tên\" vào xin_nghi_buoi_nay để chỉ đúng 1 người).")

    if xin_nghi_unresolved:
        out.append("")
        for item in xin_nghi_unresolved:
            raw, cands = item["raw"], item["candidates"]
            if cands:
                cand_str = "; ".join(
                    f'{c["name"]}' + (f' (STT{n})' if (n := (c.get("stt") if c.get("stt") is not None else c.get("login_num"))) is not None else '')
                    for c in cands)
                out.append(
                    f'❓ CẦN XÁC NHẬN: tên xin nghỉ "{raw}" không khớp Y HỆT ai, chỉ khớp MỘT PHẦN '
                    f'với {len(cands)} bạn trong danh sách lớp ({cand_str}) — 2 bạn này là 2 học sinh '
                    f'KHÁC NHAU (không phải trùng tên thật), không đủ căn cứ để tự chọn 1 người (không '
                    f'kèm STT, hoặc STT không khớp ai trong số đó). Đang tạm KHÔNG tính bạn nào ở đây '
                    f'là vắng có phép (rơi vào mục "vắng không phép" ở trên) — hãy xác nhận đúng là '
                    f'bạn nào rồi truyền lại dạng "STT. Tên".')
            else:
                out.append(
                    f'❓ CẦN XÁC NHẬN: không tìm thấy ai trong danh sách lớp khớp (kể cả một phần) với '
                    f'tên xin nghỉ "{raw}" — có thể do gõ thiếu/sai tên, tên đã đổi, hoặc là học sinh '
                    f'MỚI chưa có trong danh sách lớp. Đang tạm KHÔNG tính là vắng có phép (rơi vào mục '
                    f'"vắng không phép" ở trên) — hãy kiểm tra lại nội dung Zalo gốc và cho biết chính '
                    f'xác là ai (hoặc bổ sung vào roster nếu là học sinh mới).')

    return "\n".join(out), active_keys, present_keys

def filter_roster_by_code(roster, code):
    if not code:
        return roster
    code_n = norm(code)
    return [r for r in roster if code_n in norm(r.get("note", ""))]

# ======================= ĐỐI CHIẾU FILE PARTICIPANTS ZOOM =======================
PAREN_RE = re.compile(r"^(.*?)\s*\((.*?)\)\s*$")

def _split_paren(raw_name):
    """'129. Trương Quang Vĩnh (Trương Vĩnh)' -> ('129. Trương Quang Vĩnh', 'Trương Vĩnh').
    Phần trong ngoặc thường là biệt danh/tên phụ do Zoom lưu, dùng làm gợi ý
    thêm khi so khớp (giống hệt cách dùng message_texts cho chat)."""
    m = PAREN_RE.match(raw_name.strip())
    if m and m.group(1).strip():
        return m.group(1).strip(), m.group(2).strip()
    return raw_name.strip(), None

def build_zoom_cross_reference(roster, match, participants_rows, active_keys, present_keys,
                                student_msgs, student_entry, unmatched_chat, sender_resolution,
                                extra_teacher_names=None, da_nghi_keys=None):
    """
    Đối chiếu danh sách "Save Participants" của Zoom (CÓ tổng phút tham gia,
    trước đây bị bỏ qua hoàn toàn — xem load_participants) với roster, tách ra:

    (a) present_no_checkin — học sinh CHÍNH THỨC (active) có tham gia Zoom
        (có số phút > 0) nhưng KHÔNG được tính "có mặt" ở Phần B (không điểm
        danh / chat không đủ điều kiện) -> vẫn đang bị xếp "vắng", nhưng thực
        ra có vào lớp, chỉ là im lặng không chat.
    (b) strangers — bất kỳ tên nào (từ participants.csv HOẶC từ chat) không
        khớp chắc chắn với AI trong roster và không phải tài khoản GV/host ->
        người lạ thật sự, gộp chung tổng phút Zoom + số tin nhắn nếu có ở cả
        2 nguồn (vd cùng 1 người vừa xuất hiện trong participants vừa có chat).
    (b2) học sinh ĐÃ NGHỈ HẲN (`da_nghi_keys`, lấy từ `da_nghi_han` trong
        cấu hình lớp) mà vẫn XUẤT HIỆN trong phòng học (có phút Zoom > 0 hoặc có
        tin nhắn/lượt phát biểu) — theo yêu cầu trợ giảng (19/09/2026), các bạn
        này được đưa vào CHUNG mục người lạ (b) kèm ghi chú "(học sinh đã nghỉ)"
        để trợ giảng nhận biết ngay; KHÔNG tính vào sĩ số Phần B. Trước đây các
        bạn này bị bỏ qua hoàn toàn ở Phần C (không phải active nên không vào
        (a), khớp roster nên cũng không vào (b)).
    (c) split_identity_warnings — CẢNH BÁO MỚI, tối ưu riêng cho trường hợp
        trợ giảng CHỈ gửi 1 file participants.csv duy nhất (bản ĐÃ chọn "hiện
        thị người dùng duy nhất" khi export, không kèm file log thô từng lượt
        vào/ra). Phát hiện thực tế: dù đã xuất "duy nhất", Zoom đôi khi vẫn
        giữ CÙNG 1 TÊN HIỂN THỊ thành ≥2 DÒNG RIÊNG (ví dụ "hà tuấn,,134,Có" và
        "hà tuấn,,4,Có") — đây là chính ZOOM tự xác nhận (dựa trên ID phiên/
        thiết bị nội bộ mà mình không có quyền truy cập) rằng có thể là 2 kết
        nối/2 người khác nhau, đáng tin hơn hẳn bất kỳ suy đoán nào tự làm từ
        tên hiển thị. Trước đây script cộng thẳng phút của TẤT CẢ các dòng
        trùng tên này vào 1 học sinh mà không cảnh báo gì (ca thực tế: 134 +
        4 = 138 phút bị dồn hết cho 1 mình "159. Hà Tuấn"), làm mất chính tín
        hiệu quý giá đó. Giờ vẫn CỘNG DỒN như cũ (không tự ý đổi sĩ số/số phút
        khi chưa chắc chắn) nhưng LUÔN cảnh báo riêng để trợ giảng tự xác nhận
        có đúng là cùng 1 người hay cần tách ra.

    Mỗi dòng chat sender đã được nhận diện CHẮC CHẮN (không ambiguous) trong
    sender_resolution sẽ được tái sử dụng thẳng cho participants.csv nếu tên
    trùng khớp, thay vì so khớp lại từ đầu — tránh lặp lại lỗi mơ hồ tên (vd
    "An Khang" một mình sẽ hoà điểm giữa 2 người, nhưng chat đã xác định chắc
    chắn là ai nhờ nội dung tự giới thiệu).
    """
    dup_names = find_duplicate_names(roster)
    chat_resolution_by_norm = {}
    for sender, (entry, amb) in sender_resolution.items():
        if entry is not None and not amb:
            chat_resolution_by_norm[norm(sender)] = entry

    minutes_by_key = defaultdict(float)
    minutes_unmatched = defaultdict(float)
    unmatched_label = {}
    # Đếm số DÒNG RIÊNG (không phải tổng phút) trong participants.csv đã gộp
    # vào CÙNG 1 học sinh mà lại CÙNG 1 TÊN HIỂN THỊ Y HỆT (sau chuẩn hoá) —
    # xem giải thích đầy đủ ở split_identity_warnings trong docstring trên.
    matched_raw_counts = defaultdict(int)
    matched_raw_minutes = defaultdict(list)
    matched_raw_label = {}
    matched_raw_display = {}

    for row in participants_rows:
        raw = row["raw_name"]
        if is_teacher(raw, extra_teacher_names) or norm(row.get("guest", "")) in ("khong", "no"):
            continue
        main, paren = _split_paren(raw)

        resolved = chat_resolution_by_norm.get(norm(main))
        if resolved is None and paren:
            resolved = chat_resolution_by_norm.get(norm(paren))

        from_chat_evidence = resolved is not None
        if resolved is not None:
            m, score, amb = resolved, 999, False
        else:
            hints = [paren] if paren else []
            m, score, amb = match(main, message_texts=hints)
            if (m is None or amb or score <= 0.5) and paren:
                m2, score2, amb2 = match(paren, message_texts=[main])
                if m2 is not None and not amb2 and score2 > 0.5:
                    m, score, amb = m2, score2, amb2

        matched_ok = m is not None and not amb and score > 0.5
        if matched_ok and not from_chat_evidence:
            # Không có bằng chứng chat củng cố (participants.csv không có nội
            # dung gì khác để đối chiếu) -> chỉ tin so khớp tên hiển thị Zoom
            # đơn thuần nếu TÊN GỌI (từ cuối cùng trong tên đầy đủ — phần định
            # danh nhất trong tên người Việt) THỰC SỰ xuất hiện trong tên Zoom
            # đang xét. Vd "An Khang" chứa "Minh" của "21. Trần An Khang" -> vẫn
            # gộp OK (đủ căn cứ). Ca lỗi thực tế: "Ánh Lan" (2 phút) từng bị
            # gộp nhầm vào "Nguyễn Lan Anh Duy" (84 phút, điểm khớp 1.5) dù
            # tên gọi thật "Duy" không hề xuất hiện, chỉ trùng 2 âm tiết bị
            # đảo thứ tự ("Ngọc", "Anh") — không đủ căn cứ để cộng dồn, phải
            # tách ra thành người lạ riêng cho trợ giảng tự xác minh.
            given = _name_last_n_tokens(norm(m["name"]), 1)
            cand_tokens = set(norm(strip_stt(main)).split())
            if not (given and given in cand_tokens):
                matched_ok = False

        if matched_ok:
            minutes_by_key[entry_key(m)] += row["minutes"]
            rk = norm(raw)
            matched_raw_counts[rk] += 1
            matched_raw_minutes[rk].append(row["minutes"])
            matched_raw_label[rk] = display_label(m, dup_names)
            matched_raw_display.setdefault(rk, raw)
        else:
            k = norm(main)
            minutes_unmatched[k] += row["minutes"]
            if k not in unmatched_label or len(raw) > len(unmatched_label[k]):
                unmatched_label[k] = raw

    split_identity_warnings = []
    for rk, cnt in matched_raw_counts.items():
        if cnt > 1:
            split_identity_warnings.append({
                "raw": matched_raw_display[rk],
                "label": matched_raw_label[rk],
                "minutes_list": matched_raw_minutes[rk],
            })
    split_identity_warnings.sort(key=lambda w: -sum(w["minutes_list"]))

    present_no_checkin = []
    for k in active_keys:
        if k in present_keys:
            continue
        mins = minutes_by_key.get(k, 0)
        if mins > 0:
            entry = student_entry.get(k) or next((r for r in roster if entry_key(r) == k), None)
            present_no_checkin.append({
                "label": display_label(entry, dup_names) if entry else "?",
                "minutes": round(mins, 1),
                "so_tin": student_msgs.get(k, 0),
            })
    present_no_checkin.sort(key=lambda x: (-x["minutes"], x["label"]))

    stranger_keys = set(minutes_unmatched)
    chat_unmatched_by_norm = {norm(sender): (sender, cnt) for sender, cnt, _ in unmatched_chat}
    stranger_keys |= set(chat_unmatched_by_norm)

    strangers = []
    for k in stranger_keys:
        label = unmatched_label.get(k) or chat_unmatched_by_norm.get(k, (k, 0))[0]
        strangers.append({
            "label": label,
            "minutes": round(minutes_unmatched.get(k, 0), 1),
            "so_tin": chat_unmatched_by_norm.get(k, (None, 0))[1],
        })
    if da_nghi_keys:
        roster_by_key = {entry_key(r): r for r in roster}
        for k in da_nghi_keys:
            mins = minutes_by_key.get(k, 0)
            msgs = student_msgs.get(k, 0)
            entry = student_entry.get(k) or roster_by_key.get(k)
            if entry is None or (mins <= 0 and msgs <= 0):
                continue
            strangers.append({
                "label": f"{display_label(entry, dup_names)} ⚠️ (học sinh đã nghỉ)",
                "minutes": round(mins, 1),
                "so_tin": msgs,
            })
    strangers.sort(key=lambda x: (-x["minutes"], x["label"]))

    return present_no_checkin, strangers, split_identity_warnings

# ======================= HÀM CHÍNH — GỌI HÀM NÀY TỪ SKILL =======================
def generate_report(class_name, chat_txt, participants_csv, session_date=None,
                     xin_nghi_buoi_nay=None, filter_code=None,
                     good_threshold=None, poor_threshold=None,
                     extra_teacher_names=None, manual_checkin_names=None,
                     vtt_txt=None, mic_excuse_names=None,
                     mic_call_confirmed_penalty=None, mic_call_window_seconds=300,
                     detect_mic_call_no_response=False,
                     roster_csv=None, fixed_notes=None, roster_id_regex=None):
    """
    class_name        : tên lớp, chỉ dùng để ghi tiêu đề báo cáo (vd "Lớp A1")
    roster_csv        : đường dẫn file danh sách lớp (CSV) — BẮT BUỘC (bản web)
    fixed_notes       : dict tuỳ chọn {"da_nghi_han": [tên...], "hoc_thu": [tên...],
                          "always_absent_on_weekday": {tên: "Chủ Nhật"}}
    roster_id_regex   : regex lấy số thứ tự từ cột "Tên đăng nhập" (nhóm 1); để
                          trống thì tự đoán từ tên lớp / phần chung của các tên đăng nhập
    chat_txt          : đường dẫn file "Save Chat" từ Zoom (buổi này) — CÓ THỂ
                          truyền 1 đường dẫn (str) HOẶC 1 list nhiều đường dẫn
                          nếu có nhiều người tự export chat riêng cho cùng
                          buổi (xem Vấn đề 1/4/5: tin riêng khác nhau giữa các
                          file, người join muộn thiếu tin công khai đầu buổi
                          — gộp nhiều file giúp lấy đủ dữ liệu, script tự lo
                          việc loại trùng, không đếm đúp).
    participants_csv  : đường dẫn file "Save Participants" từ Zoom (buổi này)
    session_date      : "dd/mm/yyyy" — để trống thì tự đoán từ mốc thời gian sớm nhất trong (các) file chat
    xin_nghi_buoi_nay : list tên học sinh xin nghỉ RIÊNG buổi này (đọc từ ảnh Zalo)
    filter_code       : None (mặc định, báo cáo tổng) hoặc "R33"/"3T"... để lọc riêng nhóm đó
    extra_teacher_names: tên GV/TG PHỤ TRÁCH BUỔI NÀY (ngoài danh sách cố định dùng
                          chung mọi buổi trong TEACHER_SUBSTR_NORM) — GV/TG có thể đổi
                          theo từng buổi nên không lưu cứng theo lớp. NÊN truyền HỌ TÊN
                          ĐẦY ĐỦ thay vì chỉ 1 từ/tên gọi ngắn khi có thể (is_teacher()
                          so khớp theo TỪ nguyên vẹn — 1 tên ngắn như "An"/"Anh" vẫn có
                          thể trùng với học sinh có đúng từ đó làm tên/tên đệm).
    manual_checkin_names: list tên học sinh (hoặc "STT. Tên" nếu trùng tên, giống
                          cú pháp penalty_names) được người dùng XÁC NHẬN THỦ CÔNG là
                          đã điểm danh buổi này (vd người dùng biết chắc học sinh có
                          mặt/đã báo danh qua kênh khác), dù thuật toán tự động không
                          phát hiện được câu điểm danh rõ ràng, kể cả khi học sinh đó
                          KHÔNG có tin nhắn/lượt phát biểu nào trong buổi (0 tin vẫn
                          ép được — khớp trực tiếp với roster, không cần đã xuất hiện
                          trong dữ liệu buổi học). Ghi đè student_checkin cho (các) tên
                          này — không ảnh hưởng gì khác (không tự cộng thêm "Số tin").
    vtt_txt           : (tuỳ chọn) đường dẫn file transcript .vtt do Zoom cloud
                          recording tạo, HOẶC list nhiều file — dùng để bắt các
                          lượt tương tác bằng giọng nói (mở mic, không chat).
                          Mỗi lượt phát biểu được cộng vào tổng "Số tin" (không
                          đổi ngưỡng tương tác) + hiển thị thêm cột riêng
                          "Số lượt phát biểu" (xem Vấn đề 3). Để None nếu buổi
                          này không có file transcript.
    mic_excuse_names  : (tuỳ chọn) list tên học sinh đã xin phép KHÔNG MỞ MIC
                          buổi này qua Zalo (khác với xin_nghi_buoi_nay — vẫn có
                          mặt/vẫn tính sĩ số bình thường, chỉ là không mở mic
                          được), do bạn tự đọc tin nhắn Zalo mà trích ra, TƯƠNG
                          TỰ cách làm với xin_nghi_buoi_nay. Dùng để loại các ca
                          "bị gọi tên nhưng không trả lời" đã có lý do chính đáng.
    mic_call_confirmed_penalty: (tuỳ chọn) list tên (hoặc "STT. Tên" nếu trùng
                          tên) mà BẠN đã tự xem lại danh sách nghi vấn "GV gọi
                          tên nhưng không trả lời" ở lần chạy trước và XÁC NHẬN
                          đúng — mỗi tên trong đây bị trừ 5 khỏi tổng "Số tin"
                          (không xuống dưới 0). Để trống ở lần chạy đầu (chỉ
                          xem danh sách nghi vấn), truyền vào ở lần chạy sau
                          khi đã xác nhận xong.
    mic_call_window_seconds: số giây tính là "cửa sổ chờ phản hồi" sau khi bị
                          gọi tên bằng giọng nói trong .vtt (mặc định 300 = 5 phút).
    detect_mic_call_no_response: False (MẶC ĐỊNH, đã đổi) = tính năng này đang
                          TẮT theo mặc định — chỉ đang trong giai đoạn tiếp tục
                          cải tiến, CHƯA đưa vào dùng thật. Đặt True tường minh
                          nếu thật sự muốn bật lại cho buổi đó (vd để tiếp tục
                          thử nghiệm/cải tiến), khi có vtt_txt.
    Trả về: chuỗi text báo cáo hoàn chỉnh (Phần A + Phần B + Phần C [+ nghi vấn
    gọi tên không trả lời nếu có .vtt]), sẵn sàng gửi Zalo.
    """
    fixed = dict(fixed_notes or {})
    cfg = {"good_threshold": DEFAULT_GOOD_THRESHOLD, "poor_threshold": DEFAULT_POOR_THRESHOLD}
    if not roster_csv:
        raise ValueError("Thiếu file danh sách lớp (roster_csv).")
    roster_full = load_roster(roster_csv,
                              roster_id_regex or guess_id_regex(roster_csv, class_name),
                              extra_students=fixed.get("extra_students"))
    roster = filter_roster_by_code(roster_full, filter_code)
    match = build_matcher(roster_full)

    # Đọc trước cues thô của .vtt (nếu có) để TỰ NHẬN DIỆN GV ngay từ đầu, dựa vào
    # người có SỐ LƯỢT PHÁT BIỂU NHIỀU NHẤT VÀ TỔNG THỜI LƯỢNG PHÁT BIỂU NHIỀU
    # NHẤT trong buổi (xem detect_teacher_from_vtt_dominance) — làm TRƯỚC load_chat
    # để tên GV phát hiện được cũng được loại đúng luôn cả khỏi chat (nếu GV đó
    # có nhắn tin bằng đúng tên hiển thị này), không chỉ khỏi phần lời thoại.
    # Chỉ kết luận khi cả 2 điều kiện cùng rơi vào ĐÚNG 1 người; nếu phát hiện
    # được, GHÉP thêm vào extra_teacher_names CHO ĐÚNG buổi này (không sửa danh
    # sách GV cố định trong cấu hình lớp), đồng thời báo rõ trong kết quả để trợ
    # giảng tự xác nhận lại nếu sai.
    all_cues = []
    auto_detected_teacher = None
    if vtt_txt:
        vtt_paths = [vtt_txt] if isinstance(vtt_txt, str) else list(vtt_txt)
        for vp in vtt_paths:
            all_cues.extend(parse_vtt_cues(vp))
        all_cues.sort(key=lambda c: c["start"])

        detection = detect_teacher_from_vtt_dominance(all_cues, known_teacher_names=extra_teacher_names)
        if detection:
            auto_name, auto_turns, auto_duration = detection
            extra_teacher_names = list(extra_teacher_names or []) + [auto_name]
            auto_detected_teacher = (auto_name, auto_turns, auto_duration)

    counts, texts, guessed_date = load_chat(chat_txt, extra_teacher_names=extra_teacher_names)
    session_date = session_date or guessed_date or "(chưa rõ ngày)"
    participants_rows = load_participants(participants_csv)

    speak_counts = None
    if vtt_txt:
        vtt_paths = [vtt_txt] if isinstance(vtt_txt, str) else list(vtt_txt)
        speak_counts = defaultdict(int)
        for vp in vtt_paths:
            for speaker, cnt in load_vtt_transcript(vp, extra_teacher_names=extra_teacher_names).items():
                speak_counts[speaker] += cnt

    # restrict_keys: khi có filter_code, chỉ những học sinh THUỘC roster đã lọc theo
    # mã đó mới được đưa vào bảng xếp hạng Phần A (sửa lỗi cũ: Phần A trước đây bỏ
    # qua filter_code, hiện cả người ngoài nhóm).
    restrict_keys = {entry_key(r) for r in roster} if filter_code else None
    (table_md, student_msgs, student_checkin, unmatched, student_entry,
     sender_resolution, student_speak, speak_unmatched, special_cases) = build_ranking_table(
        roster, match, counts, texts, speak_counts=speak_counts, roster_full=roster_full,
        manual_checkin_names=manual_checkin_names, restrict_keys=restrict_keys,
        penalty_names=mic_call_confirmed_penalty)

    mic_call_candidates = []
    if vtt_txt and detect_mic_call_no_response and all_cues:
        chat_ts_entries = load_chat_timestamped(chat_txt, extra_teacher_names=extra_teacher_names)
        # alias_map: cho phép nhận diện GV gọi tên theo NICKNAME ZOOM (không chỉ tên
        # thật trong danh sách lớp) — xem collect_known_aliases().
        alias_map = collect_known_aliases(roster_full, extra_teacher_names,
                                            sender_resolution, all_cues, match)
        mic_call_candidates = detect_calls_without_response(
            all_cues, alias_map, match, chat_ts_entries,
            extra_teacher_names=extra_teacher_names, mic_excuse_names=mic_excuse_names,
            xin_nghi_buoi_nay=xin_nghi_buoi_nay, window_seconds=mic_call_window_seconds)
        if restrict_keys is not None:
            # Rà soát độ chính xác: khi đang xuất báo cáo RIÊNG 1 nhóm con (filter_code,
            # vd chỉ "R33"), danh sách nghi vấn trước đây vẫn quét TOÀN BỘ roster_full
            # (cả học sinh ngoài nhóm) — gây lệch phạm vi so với tiêu đề "(chỉ nhóm X)"
            # của báo cáo. Giữ lại ca có entry xác định THUỘC nhóm đang lọc; ca "trùng
            # tên nhiều học sinh" (entry=None) không lọc được theo nhóm nên vẫn giữ để
            # trợ giảng tự đánh giá, thà thấy thừa còn hơn bỏ sót 1 ca thật của nhóm.
            mic_call_candidates = [c for c in mic_call_candidates
                                    if c["entry"] is None or entry_key(c["entry"]) in restrict_keys]
    title_suffix = f" (chỉ nhóm {filter_code})" if filter_code else ""
    summary, active_keys, present_keys = build_summary(
        roster, fixed, xin_nghi_buoi_nay, session_date, student_msgs, student_checkin,
        student_entry, good_threshold or cfg["good_threshold"],
        poor_threshold or cfg["poor_threshold"], title_suffix=title_suffix,
        filter_code=filter_code)

    present_no_checkin, strangers, split_identity_warnings = build_zoom_cross_reference(
        roster, match, participants_rows, active_keys, present_keys,
        student_msgs, student_entry, unmatched, sender_resolution,
        extra_teacher_names=extra_teacher_names,
        da_nghi_keys={entry_key(r) for r in roster
                       if norm(r["name"]) in {norm(n) for n in fixed.get("da_nghi_han", [])}})


    label = class_name + (f" — nhóm {filter_code}" if filter_code else "")
    header = f"# LỚP {label} — {session_date}\n"
    result = f"{header}\n## PHẦN A — Bảng xếp hạng tin nhắn\n\n{table_md}\n\n## PHẦN B — Báo cáo tổng quan\n\n{summary}\n"
    result += ("\n*(Lưu ý: Phần A liệt kê MỌI người có chat, kể cả học sinh đã nghỉ hẳn/học thử "
               "(không tính vào sĩ số Phần B) — không phải lỗi, đây là 2 mục đích khác nhau: "
               "Phần A theo dõi tương tác, Phần B là điểm danh chính thức.)*\n")

    result += "\n## PHẦN C — Đối chiếu với Zoom (participants.csv)\n"
    result += "\n**Có mặt trong Zoom nhưng CHƯA điểm danh trong chat:**\n"
    if present_no_checkin:
        result += "| Họ và tên | Phút tham gia Zoom | Số tin nhắn |\n|---|---:|:---:|\n"
        for r in present_no_checkin:
            result += f"| {r['label']} | {r['minutes']} | {r['so_tin']} |\n"
    else:
        result += "(không có ai — tất cả người vào Zoom đều đã điểm danh)\n"

    result += "\n**Người lạ xuất hiện trong Zoom (không khớp danh sách lớp, kèm học sinh ĐÃ NGHỈ mà vẫn vào phòng — có ghi chú):**\n"
    if strangers:
        result += "| Tên hiển thị Zoom | Phút tham gia Zoom | Số tin nhắn |\n|---|---:|:---:|\n"
        for r in strangers:
            result += f"| {r['label']} | {r['minutes']} | {r['so_tin']} |\n"
    else:
        result += "(không phát hiện người lạ nào, cũng không có học sinh đã nghỉ vào phòng)\n"

    if unmatched:
        result += "\n⚠️ Có tên trong chat chưa khớp chắc chắn với danh sách gốc (đã đưa vào bảng xếp hạng, cần bạn xác nhận thủ công): "
        result += ", ".join(s for s, c, t in unmatched) + "."

    if split_identity_warnings:
        result += ("\n⚠️ Tên hiển thị Zoom xuất hiện thành NHIỀU DÒNG RIÊNG trong participants.csv "
                    "dù cùng 1 tên (Zoom tự giữ tách biệt — có thể là 2 kết nối/thiết bị khác nhau, "
                    "KHÔNG phải suy đoán của hệ thống) — đã cộng dồn TẠM vào 1 học sinh như dưới đây, "
                    "cần bạn xác nhận lại có đúng là cùng 1 người không: ")
        parts = []
        for w in split_identity_warnings:
            mins_str = " + ".join(f"{m:g}p" for m in w["minutes_list"])
            parts.append(f"\"{w['raw']}\" ({mins_str} → đang tính hết cho {w['label']})")
        result += "; ".join(parts) + "."

    if speak_unmatched:
        result += "\n⚠️ Có người phát biểu bằng giọng nói trong file transcript (.vtt) chưa khớp chắc chắn " \
                   "với danh sách lớp (đã đưa vào bảng xếp hạng, cần bạn xác nhận thủ công): "
        result += ", ".join(f"{s} ({c} lượt)" for s, c in speak_unmatched) + "."

    if auto_detected_teacher:
        auto_name, auto_turns, auto_duration = auto_detected_teacher
        result += (f"\n\nℹ️ Đã TỰ ĐỘNG nhận diện **{auto_name}** là giảng viên buổi này từ file .vtt "
                   f"({auto_turns} lượt phát biểu, tổng {_fmt_vtt_time(auto_duration)} — vượt trội mọi người khác "
                   f"cả về số lượt lẫn thời lượng), nên đã loại khỏi Phần A/C. Nếu KHÔNG đúng, hãy khai lại "
                   f"`extra_teacher_names` cho đúng người và chạy lại.\n")

    if special_cases:
        dup_names_full = find_duplicate_names(roster_full)
        result += ("\n\n## ⚠️ Trường hợp đặc biệt: tài khoản Zoom trùng tên hiển thị nhưng KHÁC người "
                    "(cần bạn xác nhận lại)\n")
        result += ("Có tài khoản Zoom hiển thị tên giống 1 học sinh trong danh sách, nhưng nội dung tự giới "
                   "thiệu trong chat lại khớp Y HỆT tên đầy đủ của 1 học sinh KHÁC — đã ưu tiên tính theo tên "
                   "tự giới thiệu (đáng tin hơn tên hiển thị Zoom), KHÔNG gộp chung 2 người vào 1:\n\n")
        for sender, original_entry, declared_entry in special_cases:
            orig_label = display_label(original_entry, dup_names_full)
            declared_label = display_label(declared_entry, dup_names_full)
            result += (f"- Tài khoản Zoom tên hiển thị **\"{sender}\"** (giống {orig_label}) đã tự giới thiệu "
                       f"là **{declared_label}** trong chat → đã tính tin nhắn này cho {declared_label}, KHÔNG "
                       f"phải {orig_label}.\n")

    if mic_call_candidates:
        dup_names_full = find_duplicate_names(roster_full)
        result += ("\n\n## ⚠️ Nghi vấn: GV gọi tên bằng giọng nói nhưng không thấy phản hồi "
                    "(CẦN BẠN TỰ XÁC NHẬN — KHÔNG tự trừ điểm)\n")
        result += ("Đây là suy đoán từ nội dung lời nói trong .vtt (khớp tên + cửa sổ thời gian phản hồi "
                    "ước lượng), có thể sai — vd GV chỉ nhắc tên ai đó chứ không thực sự gọi trả lời, "
                    "hoặc học sinh trả lời ngoài khung giờ ước lượng. Xem lại từng ca bên dưới, nếu đúng thì "
                    "gọi lại generate_report với `mic_call_confirmed_penalty=[...]` để trừ 5 điểm.\n\n")
        for idx, c in enumerate(mic_call_candidates, start=1):
            t_str = _fmt_vtt_time(c["start"])
            snippet = c["content"].strip()
            if len(snippet) > 150:
                snippet = snippet[:150] + "..."
            if c["entry"] is None:
                result += (f"{idx}. [~{t_str} trong file .vtt] Tên bị gọi trùng giữa: "
                           f"{', '.join(c['ambiguous_names'])} — không xác định được là ai. "
                           f"Câu GV nói: \"{snippet}\"\n")
            else:
                label = display_label(c["entry"], dup_names_full)
                result += f"{idx}. [~{t_str} trong file .vtt] {label} — {c['reason']} Câu GV nói: \"{snippet}\"\n"

    return result
