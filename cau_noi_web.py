# -*- coding: utf-8 -*-
"""Lớp kết nối giữa trang web (index.html) và bộ máy tao_bao_cao.py.
Nhận/trả JSON dạng chuỗi để gọi dễ dàng từ JavaScript qua Pyodide."""
import json, re, traceback
import tao_bao_cao as T

def preview_roster(roster_path, class_name=""):
    """Đọc danh sách lớp để hiện ô chọn 'xin nghỉ' trên trang web."""
    try:
        rx = T.guess_id_regex(roster_path, class_name)
        roster = T.load_roster(roster_path, rx)
        if not roster:
            return json.dumps({"ok": False, "error": "Không đọc được học sinh nào. Kiểm tra file có cột 'Họ và tên' và 'Tên đăng nhập' không."})
        dups = T.find_duplicate_names(roster)
        students = []
        for r in roster:
            num = T.display_num(r, dups)
            label = f"{num}. {r['name']}" if num is not None else r["name"]
            students.append({"name": r["name"], "label": label, "note": r.get("note", ""),
                             "send": T.display_label(r, dups)})
        codes = sorted({n.strip() for n in (r.get("note", "") for r in roster) if n.strip() and len(n.strip()) <= 12})
        return json.dumps({"ok": True, "students": students, "codes": codes,
                           "dups": sorted(dups)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"Không đọc được danh sách lớp: {e}"})

def _lines(v):
    return [x.strip() for x in (v or []) if str(x).strip()]

def run_report(params_json):
    p = json.loads(params_json)
    warnings = []
    try:
        for cp in p["chat_paths"]:
            entries, _ = T._parse_chat_file(cp)
            if not entries:
                warnings.append("Một file chat không có tin nhắn nào đọc được — có thể không phải file 'Save Chat' của Zoom.")
        if not T.load_participants(p["participants_path"]):
            warnings.append("File người tham gia không đọc được dòng nào — phần đối chiếu Zoom (Phần C) sẽ trống.")
        notes = p.get("fixed_notes") or {}
        fixed = {"da_nghi_han": _lines(notes.get("da_nghi_han")), "hoc_thu": _lines(notes.get("hoc_thu")),
                 "always_absent_on_weekday": {k: v for k, v in (notes.get("always_absent_on_weekday") or {}).items() if k and v}}
        report = T.generate_report(
            p.get("class_name") or "Lớp", p["chat_paths"], p["participants_path"],
            session_date=p.get("session_date") or None,
            xin_nghi_buoi_nay=_lines(p.get("excused")),
            filter_code=(p.get("filter_code") or "").strip() or None,
            good_threshold=p.get("good") or None, poor_threshold=p.get("poor") or None,
            extra_teacher_names=_lines(p.get("teachers")) or None,
            manual_checkin_names=_lines(p.get("manual_checkin")) or None,
            vtt_txt=p.get("vtt_paths") or None,
            roster_csv=p["roster_path"], fixed_notes=fixed)
        return json.dumps({"ok": True, "report": report, "warnings": warnings}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}", "detail": traceback.format_exc()[-600:]}, ensure_ascii=False)
