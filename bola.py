import os
import io
import time
import html
import calendar
import sqlite3
import base64
import zipfile
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from xml.sax.saxutils import escape as xml_escape
from flask import (
    Flask, request, redirect, url_for, session, flash,
    get_flashed_messages, g, jsonify, Response
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "yotoqxona-super-admin-2026-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERSIST_DIR = os.environ.get("YOTOQXONA_DATA_DIR") or os.path.join(os.path.dirname(BASE_DIR), "yotoqxona_data")
os.makedirs(PERSIST_DIR, exist_ok=True)

DB_PATH = os.environ.get("YOTOQXONA_DB_PATH") or os.path.join(PERSIST_DIR, "yotoqxona.db")
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

UPLOAD_DIR = os.environ.get("YOTOQXONA_UPLOAD_DIR") or os.path.join(PERSIST_DIR, "uploads")
TALABALAR_FILE = os.environ.get("YOTOQXONA_TALABALAR_FILE") or os.path.join(PERSIST_DIR, "talabalar.txt")
os.makedirs(UPLOAD_DIR, exist_ok=True)

_legacy_db = os.path.join(BASE_DIR, "yotoqxona.db")
if not os.path.exists(DB_PATH) and os.path.exists(_legacy_db):
    try:
        os.replace(_legacy_db, DB_PATH)
    except OSError:
        pass

_legacy_txt = os.path.join(BASE_DIR, "talabalar.txt")
if not os.path.exists(TALABALAR_FILE) and os.path.exists(_legacy_txt):
    try:
        os.replace(_legacy_txt, TALABALAR_FILE)
    except OSError:
        pass

UZ_TZ = timezone(timedelta(hours=5))

DEFAULT_STUDENTS_TXT = """# Ism Familiya | Sinf | Xona | Sig'im (6 yoki 4)
Mamadaliyev Shoxbozbek | 9-A | 12c | 4
Saidov Ruslan | 9-A | 12c | 4
Azizov Muhammadabdulloh | 5-A | 12c | 4
Egamberdiyev Inomjon | 8-A | 12c | 4
G'aybullayev Oybek | 9-B | 6c | 6
Lutfiddinov Mirobidjon | 9-A | 12c | 6
lutfiddinov Mirhodiy | 10-A | 12c | 6
"""

PINS = {
    "ustoz": os.environ.get("PIN_USTOZ", "1111"),
    "oshpaz": os.environ.get("PIN_OSHPAZ", "2222"),
    "superadmin": os.environ.get("PIN_SUPERADMIN", "9999"),
}

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
failed_logins = {}

LOGO_BADGE_HTML = (
    '<span class="brand-logo" aria-hidden="true">'
    '<svg viewBox="0 0 64 64" width="42" height="42" xmlns="http://www.w3.org/2000/svg">'
    '<defs><linearGradient id="brandGrad" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#2563eb"/>'
    '<stop offset="100%" stop-color="#0ea5e9"/>'
    '</linearGradient></defs>'
    '<rect width="64" height="64" rx="18" fill="url(#brandGrad)"/>'
    '<path d="M16 33L32 19l16 14v15H38V37H26v11H16V33z" fill="#ffffff"/>'
    '</svg>'
    '</span>'
)

FAVICON_DATA_URI = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
    "%3Crect width='64' height='64' rx='18' fill='%232563eb'/%3E"
    "%3Cpath d='M16 33L32 19l16 14v15H38V37H26v11H16V33z' fill='%23ffffff'/%3E"
    "%3C/svg%3E"
)


def now_uz():
    return datetime.now(UZ_TZ)


def esc(val):
    s = html.escape(str(val or ""), quote=True)
    return s.replace('"', "&quot;")


def get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def is_login_locked(ip):
    rec = failed_logins.get(ip)
    if not rec:
        return False, 0
    locked_until = rec.get("locked_until", 0)
    now = time.time()
    if locked_until > now:
        return True, int(locked_until - now) + 1
    if locked_until and locked_until <= now:
        failed_logins.pop(ip, None)
    return False, 0


def record_failed_login(ip):
    now = time.time()
    rec = failed_logins.get(ip, {"count": 0, "locked_until": 0})
    rec["count"] += 1
    if rec["count"] >= MAX_LOGIN_ATTEMPTS:
        rec["locked_until"] = now + LOCKOUT_SECONDS
        rec["count"] = 0
    failed_logins[ip] = rec


def clear_failed_login(ip):
    failed_logins.pop(ip, None)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def today_str():
    return now_uz().strftime("%Y-%m-%d")


UZ_MONTHS = [
    "", "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
    "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr"
]


def format_uz_date(iso_date):
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        dt = now_uz()
    weekdays = [
        "Dushanba", "Seshanba", "Chorshanba", "Payshanba",
        "Juma", "Shanba", "Yakshanba"
    ]
    return f"{dt.day}-{UZ_MONTHS[dt.month]} {dt.year}-yil, {weekdays[dt.weekday()]}"


def format_uz_month(year_num, month_num):
    m_name = UZ_MONTHS[month_num] if 1 <= month_num <= 12 else str(month_num)
    return f"{m_name} {year_num}-yil"


def auto_theme_by_hour():
    hour = now_uz().hour
    return "day" if 6 <= hour < 18 else "night"


def detect_device_mode():
    if session.get("device_mode") in ("phone", "desktop"):
        return session["device_mode"]
    ua = (request.headers.get("User-Agent") or "").lower()
    if any(k in ua for k in ("iphone", "android", "mobile", "ipod", "opera mini", "windows phone")):
        return "phone"
    return "desktop"


def ensure_talabalar_txt_exists():
    if not os.path.exists(TALABALAR_FILE):
        with open(TALABALAR_FILE, "w", encoding="utf-8") as f:
            f.write(DEFAULT_STUDENTS_TXT)


def sync_students_from_txt(db):
    ensure_talabalar_txt_exists()
    cur = db.cursor()
    with open(TALABALAR_FILE, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) < 3:
                continue
            full_name = parts[0]
            class_name = parts[1]
            room_code = parts[2].lower()
            capacity = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 6

            room = cur.execute(
                "SELECT id, capacity FROM rooms WHERE LOWER(code) = ? AND capacity = ?",
                (room_code, capacity),
            ).fetchone()
            if not room:
                room = cur.execute(
                    "SELECT id, capacity FROM rooms WHERE LOWER(code) = ? ORDER BY capacity DESC",
                    (room_code,),
                ).fetchone()
            if not room:
                continue

            exists = cur.execute(
                "SELECT id FROM students WHERE full_name = ? AND class_name = ? AND room_id = ?",
                (full_name, class_name, room["id"]),
            ).fetchone()
            occ = cur.execute(
                "SELECT COUNT(*) AS c FROM students WHERE room_id = ?",
                (room["id"],),
            ).fetchone()["c"]
            if not exists and occ < room["capacity"]:
                cur.execute(
                    "INSERT INTO students (full_name, class_name, room_id) VALUES (?, ?, ?)",
                    (full_name, class_name, room["id"]),
                )
    db.commit()


def rewrite_talabalar_txt(db):
    rows = db.execute("""
        SELECT s.full_name, s.class_name, r.code, r.capacity
        FROM students s
        JOIN rooms r ON s.room_id = r.id
        ORDER BY CAST(substr(r.code, 1, length(r.code)-1) AS INTEGER), r.capacity DESC, s.full_name
    """).fetchall()
    with open(TALABALAR_FILE, "w", encoding="utf-8") as f:
        f.write("# Ism Familiya | Sinf | Xona | Sig'im (6 yoki 4)\n")
        for r in rows:
            f.write(f"{r['full_name']} | {r['class_name']} | {r['code']} | {r['capacity']}\n")


def normalize_phone_status(val):
    v = (val or "").strip()
    mapping = {
        "Berildi": "Berildi",
        "Biz bermadik": "Biz bermadik",
        "Berilmadi": "Biz bermadik",
        "O'quvchi oldi": "O'quvchi oldi",
        "Olingan": "O'quvchi oldi",
        "O'quvchi bermadi": "O'quvchi bermadi",
    }
    return mapping.get(v, "")


def col_letter(idx_1based):
    letters = ""
    n = idx_1based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def build_xlsx_bytes(title_line, summary_line, headers, rows_data, col_widths, center_cols=None):
    center_set = set(center_cols or [])
    sheet_rows_xml = []

    def cell_xml(r_idx, c_idx, val, style_idx=4, is_num=False):
        ref = f"{col_letter(c_idx)}{r_idx}"
        if is_num and isinstance(val, (int, float)):
            return f'<c r="{ref}" s="{style_idx}"><v>{val}</v></c>'
        s_val = xml_escape(str(val if val is not None else ""))
        return f'<c r="{ref}" s="{style_idx}" t="inlineStr"><is><t xml:space="preserve">{s_val}</t></is></c>'

    sheet_rows_xml.append(
        f'<row r="1" ht="24" customHeight="1">{cell_xml(1, 1, title_line, style_idx=1)}</row>'
    )
    sheet_rows_xml.append(
        f'<row r="2" ht="20" customHeight="1">{cell_xml(2, 1, summary_line, style_idx=2)}</row>'
    )
    sheet_rows_xml.append('<row r="3" ht="10" customHeight="1"/>')

    hdr_cells = "".join(
        cell_xml(4, c + 1, h, style_idx=6 if (c + 1) in center_set else 3)
        for c, h in enumerate(headers)
    )
    sheet_rows_xml.append(f'<row r="4" ht="24" customHeight="1">{hdr_cells}</row>')

    for offset, r_vals in enumerate(rows_data):
        r_num = 5 + offset
        cells = []
        for c_idx, val in enumerate(r_vals, start=1):
            is_centered = c_idx in center_set
            if isinstance(val, int) and not isinstance(val, bool):
                st_i = 5 if (c_idx == 1 or is_centered) else 4
                cells.append(cell_xml(r_num, c_idx, val, style_idx=st_i, is_num=True))
            else:
                st_i = 5 if is_centered else 4
                cells.append(cell_xml(r_num, c_idx, val, style_idx=st_i, is_num=False))
        sheet_rows_xml.append(f'<row r="{r_num}" ht="20" customHeight="1">{"".join(cells)}</row>')

    cols_xml = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
        for i, w in enumerate(col_widths)
    )

    last_col = col_letter(len(headers))
    last_row = max(5, 4 + len(rows_data))

    worksheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews>
    <sheetView workbookViewId="0" showGridLines="1"/>
  </sheetViews>
  <cols>{cols_xml}</cols>
  <sheetData>{"".join(sheet_rows_xml)}</sheetData>
  <autoFilter ref="A4:{last_col}{last_row}"/>
  <pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>
</worksheet>"""

    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="3">
    <font><sz val="11"/><name val="Calibri"/><color theme="1"/></font>
    <font><b/><sz val="14"/><name val="Calibri"/><color theme="1"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/><color rgb="FFFFFFFF"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF2563EB"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border>
      <left style="thin"><color rgb="FFCBD5E1"/></left>
      <right style="thin"><color rgb="FFCBD5E1"/></right>
      <top style="thin"><color rgb="FFCBD5E1"/></top>
      <bottom style="thin"><color rgb="FFCBD5E1"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellXfs count="7">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" applyFont="1"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" applyFont="1"/>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" applyFont="1"/>
    <xf numFmtId="0" fontId="2" fillId="2" borderId="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" applyFont="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="2" borderId="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
  </cellXfs>
</styleSheet>"""

    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Davomat" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""

    workbook_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

    root_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml.encode("utf-8"))
        zf.writestr("_rels/.rels", root_rels_xml.encode("utf-8"))
        zf.writestr("xl/workbook.xml", workbook_xml.encode("utf-8"))
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml.encode("utf-8"))
        zf.writestr("xl/styles.xml", styles_xml.encode("utf-8"))
        zf.writestr("xl/worksheets/sheet1.xml", worksheet_xml.encode("utf-8"))
    return buf.getvalue()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    cur = db.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            sub_label TEXT NOT NULL,
            capacity INTEGER NOT NULL,
            UNIQUE(code, sub_label)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            class_name TEXT NOT NULL,
            room_id INTEGER NOT NULL,
            FOREIGN KEY (room_id) REFERENCES rooms (id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            work_date TEXT NOT NULL,
            status TEXT DEFAULT '',
            reason_type TEXT DEFAULT '',
            reason_note TEXT DEFAULT '',
            phone_status TEXT DEFAULT '',
            phone_given INTEGER DEFAULT 0,
            phone_photo TEXT DEFAULT '',
            phone_time TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            UNIQUE(student_id, work_date),
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE
        )
    """)

    cols = [r[1] for r in cur.execute("PRAGMA table_info(attendance)").fetchall()]
    if "phone_status" not in cols:
        cur.execute("ALTER TABLE attendance ADD COLUMN phone_status TEXT DEFAULT ''")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS supervisors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_number INTEGER UNIQUE NOT NULL,
            teacher_name TEXT NOT NULL,
            shift_start TEXT NOT NULL,
            shift_end TEXT NOT NULL,
            on_time INTEGER DEFAULT 0,
            arrived_at TEXT DEFAULT ''
        )
    """)

    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_theme', '1')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('last_seen_date', ?)", (today_str(),))

    cur.execute("SELECT COUNT(*) FROM supervisors")
    if cur.fetchone()[0] == 0:
        default_supervisors = [
            (1, "1-Ustoz", "07:00", "15:00", 0, ""),
            (2, "2-Ustoz", "15:00", "23:00", 0, ""),
            (3, "3-Ustoz", "23:00", "07:00", 0, ""),
        ]
        cur.executemany("""
            INSERT INTO supervisors (slot_number, teacher_name, shift_start, shift_end, on_time, arrived_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, default_supervisors)

    cur.execute("SELECT COUNT(*) FROM rooms")
    if cur.fetchone()[0] == 0:
        for i in range(1, 13):
            code = f"{i}c"
            cur.execute("INSERT OR IGNORE INTO rooms (code, sub_label, capacity) VALUES (?, '6 kishilik xona', 6)", (code,))
            cur.execute("INSERT OR IGNORE INTO rooms (code, sub_label, capacity) VALUES (?, '4 kishilik xona', 4)", (code,))

    db.commit()
    sync_students_from_txt(db)
    rewrite_talabalar_txt(db)
    db.close()


init_db()


def ensure_day_rollover():
    db = get_db()
    cur = db.cursor()
    cur_date = today_str()

    try:
        row = cur.execute("SELECT value FROM settings WHERE key = 'last_seen_date'").fetchone()
    except sqlite3.OperationalError:
        init_db()
        row = cur.execute("SELECT value FROM settings WHERE key = 'last_seen_date'").fetchone()

    last_date = row["value"] if row else cur_date
    if last_date != cur_date:
        cur.execute("UPDATE settings SET value = ? WHERE key = 'last_seen_date'", (cur_date,))
        cur.execute("UPDATE supervisors SET on_time = 0, arrived_at = ''")
        db.commit()

    sync_students_from_txt(db)


def is_time_in_shift(now_hm, start_hm, end_hm):
    if start_hm <= end_hm:
        return start_hm <= now_hm <= end_hm
    return now_hm >= start_hm or now_hm <= end_hm


def current_role():
    return session.get("role")


def role_home_url(role):
    if role == "oshpaz":
        return url_for("chef_page")
    if role == "superadmin":
        return url_for("superadmin_page")
    if role == "ustoz":
        return url_for("attendance_page")
    return url_for("login_page")


def require_login(roles=None, is_api=False):
    role = current_role()
    if not role:
        if is_api:
            return jsonify({"ok": False, "error": "Avval tizimga kiring"}), 401
        return redirect(url_for("login_page"))
    if roles and role not in roles:
        if is_api:
            return jsonify({"ok": False, "error": "Ruxsat berilmagan"}), 403
        flash("Sizda bu bo'limga kirish huquqi yo'q.", "error")
        return redirect(role_home_url(role))
    return None


def active_theme():
    db = get_db()
    if session.get("manual_theme") in ("day", "night"):
        return session["manual_theme"]
    row = db.execute("SELECT value FROM settings WHERE key = 'auto_theme'").fetchone()
    auto_enabled = (row["value"] == "1") if row else True
    return auto_theme_by_hour() if auto_enabled else "day"


def fetch_students_with_attendance(db, work_date):
    rows = db.execute("""
        SELECT s.id, s.full_name, s.class_name, s.room_id,
               r.code AS room_code, r.sub_label AS room_sub_label, r.capacity AS room_capacity,
               COALESCE(a.status, '') AS status,
               COALESCE(a.reason_type, '') AS reason_type,
               COALESCE(a.reason_note, '') AS reason_note,
               COALESCE(a.phone_status, '') AS phone_status,
               COALESCE(a.phone_given, 0) AS phone_given,
               COALESCE(a.phone_photo, '') AS phone_photo,
               COALESCE(a.phone_time, '') AS phone_time
        FROM students s
        JOIN rooms r ON s.room_id = r.id
        LEFT JOIN attendance a ON a.student_id = s.id AND a.work_date = ?
        ORDER BY CAST(substr(r.code, 1, length(r.code)-1) AS INTEGER), r.capacity DESC, s.full_name
    """, (work_date,)).fetchall()

    students = []
    for r in rows:
        d = dict(r)
        norm_ps = normalize_phone_status(d["phone_status"])
        if not norm_ps and d["phone_given"] == 1:
            norm_ps = "Berildi"
        d["phone_status"] = norm_ps
        students.append(d)
    return students


def render_page(page_name):
    ensure_day_rollover()
    db = get_db()
    role = current_role()
    theme = active_theme()
    device_mode = detect_device_mode()
    cur_today = today_str()
    selected_date = request.args.get("date") or cur_today
    try:
        datetime.strptime(selected_date, "%Y-%m-%d")
    except ValueError:
        selected_date = cur_today

    is_other_date = (selected_date != cur_today)

    class_filter = request.args.get("class_name", "ALL")
    room_code_filter = request.args.get("room_code", "ALL")

    now_dt = now_uz()
    now_hm = now_dt.strftime("%H:%M")
    now_clock_str = now_dt.strftime("%H:%M:%S")

    sup_rows = db.execute("SELECT * FROM supervisors ORDER BY slot_number").fetchall()
    supervisors = []
    active_duty_list = []
    for s in sup_rows:
        d = dict(s)
        on_duty = is_time_in_shift(now_hm, d["shift_start"], d["shift_end"])
        d["on_duty"] = on_duty
        supervisors.append(d)
        if on_duty:
            t_name = esc(d["teacher_name"])
            s_start = esc(d["shift_start"])
            s_end = esc(d["shift_end"])
            active_duty_list.append(f"{t_name} ({s_start}-{s_end})")
    duty_banner_text = ", ".join(active_duty_list) if active_duty_list else "Hozir smena vaqtidan tashqari"

    students = fetch_students_with_attendance(db, selected_date)

    classes = sorted(list({st["class_name"] for st in students}))
    room_codes = [f"{i}c" for i in range(1, 13)]

    filtered_students = students
    if class_filter != "ALL":
        filtered_students = [st for st in filtered_students if st["class_name"] == class_filter]
    if room_code_filter != "ALL":
        filtered_students = [st for st in filtered_students if st["room_code"] == room_code_filter]

    total_count = len(students)
    present_count = sum(1 for st in students if st["status"] == "+")
    absent_count = sum(1 for st in students if st["status"] == "-")
    unmarked_count = total_count - present_count - absent_count

    phone_given_count = sum(1 for st in students if st["phone_status"] == "Berildi")
    phone_not_given_by_us_count = sum(1 for st in students if st["phone_status"] == "Biz bermadik")
    student_took_count = sum(1 for st in students if st["phone_status"] == "O'quvchi oldi")
    student_not_given_count = sum(1 for st in students if st["phone_status"] == "O'quvchi bermadi")

    class_chips_html = "".join(
        f'<div class="chip"><strong>{esc(cname)}:</strong> {sum(1 for st in students if st["class_name"] == cname and st["status"] == "+")}</div>'
        for cname in classes
    )

    all_rooms = db.execute("""
        SELECT id, code, sub_label, capacity
        FROM rooms
        ORDER BY CAST(substr(code, 1, length(code)-1) AS INTEGER), capacity DESC
    """).fetchall()

    rooms_grouped = []
    for r in all_rooms:
        occupants = [st for st in students if st["room_id"] == r["id"]]
        rooms_grouped.append({
            "id": r["id"],
            "code": r["code"],
            "sub_label": r["sub_label"],
            "capacity": r["capacity"],
            "occupants": occupants,
            "occupied_count": len(occupants),
            "present_in_room": sum(1 for o in occupants if o["status"] == "+")
        })

    total_attendance_records = db.execute("SELECT COUNT(*) AS c FROM attendance WHERE status != ''").fetchone()["c"]
    total_photos = db.execute("SELECT COUNT(*) AS c FROM attendance WHERE phone_photo != ''").fetchone()["c"]
    auto_theme_row = db.execute("SELECT value FROM settings WHERE key = 'auto_theme'").fetchone()
    auto_theme_enabled = (auto_theme_row["value"] == "1") if auto_theme_row else True

    flash_html = ""
    for category, msg in get_flashed_messages(with_categories=True):
        flash_html += f'<div class="alert alert-{esc(category)}">{esc(msg)}</div>'

    role_badge_html = ""
    if role:
        r_label = {"ustoz": "Ustoz", "oshpaz": "Oshpaz", "superadmin": "Super Admin"}.get(role, role)
        logout_url = url_for("logout")
        role_badge_html = f"""
          <span class="role-pill role-{esc(role)}">{esc(r_label)}</span>
          <form method="POST" action="{logout_url}" class="inline-form">
            <button type="submit" class="btn btn-outline btn-sm">Chiqish</button>
          </form>
        """

    theme_btn_label = "Kechki rejim" if theme == "day" else "Kunduzgi rejim"
    theme_next_val = "night" if theme == "day" else "day"

    device_btn_label = "Kompyuter rejimi" if device_mode == "phone" else "Telefon rejimi"
    device_next_val = "desktop" if device_mode == "phone" else "phone"

    if page_name == "login":
        login_url = url_for("login")
        body_html = f"""
        <section class="login-wrapper">
          <div class="glass-card login-card">
            <div class="login-brand">
              {LOGO_BADGE_HTML}
              <div>
                <h1>Tizimga kirish</h1>
                <p class="muted">Rolni tanlang va PIN-kodni kiriting.</p>
              </div>
            </div>
            <form method="POST" action="{login_url}">
              <label class="field-label">Rolni tanlang</label>
              <div class="role-select-grid">
                <label class="role-option">
                  <input type="radio" name="role" value="ustoz" checked />
                  <span><strong>Ustoz</strong><small>Tarbiyachi</small></span>
                </label>
                <label class="role-option">
                  <input type="radio" name="role" value="oshpaz" />
                  <span><strong>Oshpaz</strong><small>Oshxona</small></span>
                </label>
                <label class="role-option">
                  <input type="radio" name="role" value="superadmin" />
                  <span><strong>Super Admin</strong><small>Nazorat</small></span>
                </label>
              </div>
              <label class="field-label" for="pin">PIN-kod</label>
              <input id="pin" type="password" name="pin" class="input" placeholder="PIN-kodni kiriting" required />
              <button type="submit" class="btn btn-primary full-width mt-12">Kirish</button>
            </form>
          </div>
        </section>
        """
    elif page_name == "chef":
        chef_url = url_for("chef_page", date=selected_date)
        body_html = f"""
        <section class="chef-section glass-card">
          <div class="chef-date">{esc(format_uz_date(selected_date))}</div>
          <div class="chef-label">Yotoqxonada bor o'quvchilar soni</div>
          <div class="chef-big-number">{present_count} <span>nafar</span></div>
          <div class="chef-substats">
            <div>Jami: <strong>{total_count}</strong></div>
            <div>Yo'q (-): <strong>{absent_count}</strong></div>
            <div>Belgilanmagan: <strong>{unmarked_count}</strong></div>
          </div>
          <div class="chip-row mb-16">{class_chips_html}</div>
          <a href="{chef_url}" class="btn btn-primary">Yangilash</a>
        </section>
        """
    else:
        super_url = url_for("superadmin_page", date=selected_date)
        super_active = "active" if page_name == "superadmin" else ""
        super_tab = (
            f'<a href="{super_url}" class="nav-tab {super_active}">5. Super Admin</a>'
            if role == "superadmin" else ""
        )
        endpoint_name = request.endpoint if request.endpoint in (
            "attendance_page", "phones_page", "students_page", "rooms_page", "superadmin_page"
        ) else "attendance_page"
        today_url = url_for(endpoint_name, date=cur_today)
        today_btn = (
            f'<a href="{today_url}" class="btn btn-primary btn-sm">Bugunga qaytish ({esc(cur_today)})</a>'
            if is_other_date else ""
        )
        other_date_banner = (
            f'<div class="alert alert-warn">Diqqat: Siz hozir <strong>{esc(selected_date)}</strong> sanasidagi davomatni ko\'ryapsiz (Bugungi sana: <strong>{esc(cur_today)}</strong>).</div>'
            if is_other_date else ""
        )

        sub_content = ""
        if page_name == "attendance":
            class_opts = "".join(
                f'<option value="{esc(c)}" {"selected" if class_filter == c else ""}>{esc(c)}</option>'
                for c in classes
            )
            room_opts = "".join(
                f'<option value="{esc(rc)}" {"selected" if room_code_filter == rc else ""}>{esc(rc)}</option>'
                for rc in room_codes
            )
            export_day_url = url_for(
                "export_attendance_excel_xlsx",
                date=selected_date,
                class_name=class_filter,
                room_code=room_code_filter,
            )
            sel_month_str = selected_date[:7]
            export_month_url = url_for(
                "export_attendance_monthly_xlsx",
                month=sel_month_str,
                class_name=class_filter,
                room_code=room_code_filter,
            )
            att_form_url = url_for("attendance_page")
            rows_html = ""
            for idx, st in enumerate(filtered_students, start=1):
                sid = st["id"]
                active_cls = "active-row" if idx == 1 else ""
                p_cls = "is-plus" if st["status"] == "+" else ""
                m_cls = "is-minus" if st["status"] == "-" else ""
                tag_txt = "Bor (+)" if st["status"] == "+" else ("Yo'q (-)" if st["status"] == "-" else "Belgilanmagan")
                r_disp = ""
                if st["status"] == "-":
                    r_disp = f'{esc(st["reason_type"])}: {esc(st["reason_note"])}' if st["reason_note"] else esc(st["reason_type"])
                note_hidden = "" if st["reason_type"] == "Boshqa sabab" else "hidden"
                sel_1 = "selected" if st["reason_type"] == "Sababli" else ""
                sel_2 = "selected" if st["reason_type"] == "O'zboshimchalik bilan" else ""
                sel_3 = "selected" if st["reason_type"] == "Boshqa sabab" else ""
                rows_html += f"""
                <tr class="excel-row {active_cls}" tabindex="0"
                    data-student-id="{sid}"
                    data-student-name="{esc(st["full_name"])}"
                    data-status="{esc(st["status"])}"
                    onclick="focusRow(this)">
                  <td data-label="#">{idx}</td>
                  <td data-label="O'quvchi"><strong>{esc(st["full_name"])}</strong></td>
                  <td data-label="Sinf">{esc(st["class_name"])}</td>
                  <td data-label="Xona">{esc(st["room_code"])} <small>({st["room_capacity"]} k.)</small></td>
                  <td data-label="Davomat">
                    <div class="att-actions">
                      <button type="button" class="cell-btn plus-btn {p_cls}" onclick="event.stopPropagation(); setRowPlus(this.closest('tr'))">+</button>
                      <button type="button" class="cell-btn minus-btn {m_cls}" onclick="event.stopPropagation(); openRowMinus(this.closest('tr'))">-</button>
                      <span class="status-tag" id="tag-{sid}">{tag_txt}</span>
                    </div>
                  </td>
                  <td data-label="Sabab">
                    <div id="reason-display-{sid}" class="reason-display">{r_disp}</div>
                    <div class="inline-reason-editor hidden" id="editor-{sid}" onclick="event.stopPropagation()">
                      <select class="input input-sm" id="rtype-{sid}" onchange="checkOtherInput({sid})">
                        <option value="Sababli" {sel_1}>Sababli</option>
                        <option value="O'zboshimchalik bilan" {sel_2}>O'zboshimchalik bilan</option>
                        <option value="Boshqa sabab" {sel_3}>Boshqa sabab</option>
                      </select>
                      <input type="text" class="input input-sm {note_hidden}" id="rnote-{sid}" value="{esc(st["reason_note"])}" placeholder="Izoh..." />
                      <button type="button" class="btn btn-danger btn-sm" onclick="saveRowMinus({sid}, this.closest('tr'))">Saqlash &rarr;</button>
                    </div>
                  </td>
                </tr>
                """
            all_cls_sel = "selected" if class_filter == "ALL" else ""
            all_rm_sel = "selected" if room_code_filter == "ALL" else ""
            sub_content = f"""
            <section class="glass-card section-box">
              <div class="section-header">
                <div>
                  <h2>Katta Excel Davomat Jadvali ({esc(format_uz_date(selected_date))})</h2>
                  <p class="muted">Har bir kun bazada saqlanib qoladi — 1 kunlik yoki 1 oylik (.xlsx) hisobotni istalgan payt yuklab olish mumkin.</p>
                </div>
                <div class="filter-bar">
                  <form method="GET" action="{att_form_url}" class="filter-bar">
                    <input type="hidden" name="date" value="{esc(selected_date)}" />
                    <select name="class_name" class="input input-sm" onchange="this.form.submit()">
                      <option value="ALL" {all_cls_sel}>Barcha sinflar</option>
                      {class_opts}
                    </select>
                    <select name="room_code" class="input input-sm" onchange="this.form.submit()">
                      <option value="ALL" {all_rm_sel}>1c-12c barchasi</option>
                      {room_opts}
                    </select>
                  </form>
                  <a href="{export_day_url}" class="btn btn-soft btn-sm">Kunlik Excel (.xlsx)</a>
                  <a href="{export_month_url}" class="btn btn-primary btn-sm">1 oylik Excel ({esc(sel_month_str)})</a>
                </div>
              </div>

              <div id="att-save-feedback" class="alert hidden"></div>

              <div class="table-wrap">
                <table class="excel-table excel-grid-table responsive-table" id="excel-attendance-table">
                  <thead>
                    <tr><th>#</th><th>O'quvchi</th><th>Sinf</th><th>Xona</th><th>Davomat (+ / -)</th><th>Sabab</th></tr>
                  </thead>
                  <tbody>{rows_html}</tbody>
                </table>
              </div>

              <div class="bulk-save-bar">
                <span id="att-unmarked-summary" class="muted">Holatni belgilab, saqlang.</span>
                <div class="bulk-save-actions">
                  <a href="{export_day_url}" class="btn btn-outline">Kunlik Excel (.xlsx)</a>
                  <a href="{export_month_url}" class="btn btn-soft">1 oylik Excel ({esc(sel_month_str)})</a>
                  <button type="button" id="btn-save-all-attendance" class="btn btn-primary" onclick="saveAllAttendance()">Hammasini saqlash</button>
                </div>
              </div>
            </section>
            """
        elif page_name == "phones":
            set_status_url = url_for("set_phone_status")
            set_all_url = url_for("set_all_phones_not_given")
            save_photo_url = url_for("save_phone_photo")
            p_rows = ""
            for st in students:
                ps = st["phone_status"]
                pt = esc(st["phone_time"])
                if ps == "Berildi":
                    p_badge = f'<span class="badge badge-phone">Berildi ({pt})</span>'
                elif ps == "Biz bermadik":
                    p_badge = f'<span class="badge badge-notgiven">Biz bermadik ({pt})</span>'
                elif ps == "O'quvchi oldi":
                    p_badge = f'<span class="badge badge-taken">O\'quvchi oldi ({pt})</span>'
                elif ps == "O'quvchi bermadi":
                    p_badge = f'<span class="badge badge-notgiven">O\'quvchi bermadi ({pt})</span>'
                else:
                    p_badge = '<span class="badge badge-none">Belgilanmagan</span>'

                photo_href = url_for("static", filename=st["phone_photo"]) if st["phone_photo"] else ""
                p_img = f'<a href="{photo_href}" target="_blank" class="btn btn-soft btn-sm">Ko\'rish</a>' if st["phone_photo"] else "—"

                p_rows += f"""
                <tr>
                  <td data-label="O'quvchi"><strong>{esc(st["full_name"])}</strong></td>
                  <td data-label="Sinf / Xona">{esc(st["class_name"])} &middot; {esc(st["room_code"])}</td>
                  <td data-label="Holat">{p_badge}</td>
                  <td data-label="Amallar">
                    <div class="phone-btn-group">
                      <button type="button" class="btn btn-primary btn-sm"
                        data-student-id="{st["id"]}"
                        data-student-name="{esc(st["full_name"])}"
                        data-phone-status="Berildi"
                        onclick="openCameraFromBtn(this)">Berildi (Kamera)</button>
                      <form method="POST" action="{set_status_url}" class="inline-form">
                        <input type="hidden" name="student_id" value="{st["id"]}" />
                        <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                        <input type="hidden" name="phone_status" value="Biz bermadik" />
                        <button type="submit" class="btn btn-outline btn-sm">Biz bermadik</button>
                      </form>
                      <button type="button" class="btn btn-soft btn-sm"
                        data-student-id="{st["id"]}"
                        data-student-name="{esc(st["full_name"])}"
                        data-phone-status="O'quvchi oldi"
                        onclick="openCameraFromBtn(this)">O'quvchi oldi (Kamera)</button>
                      <form method="POST" action="{set_status_url}" class="inline-form">
                        <input type="hidden" name="student_id" value="{st["id"]}" />
                        <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                        <input type="hidden" name="phone_status" value="O'quvchi bermadi" />
                        <button type="submit" class="btn btn-outline btn-sm">O'quvchi bermadi</button>
                      </form>
                    </div>
                  </td>
                  <td data-label="Isbot rasm">{p_img}</td>
                </tr>
                """
            sub_content = f"""
            <section class="glass-card section-box">
              <div class="section-header">
                <div>
                  <h2>Telefon Nazorati ({esc(format_uz_date(selected_date))})</h2>
                  <p class="muted">Berildi / Biz bermadik — biz tomonimizdan; O'quvchi oldi / O'quvchi bermadi — o'quvchi tomonidan. Vaqt Toshkent (UTC+5) bo'yicha yoziladi.</p>
                </div>
                <form method="POST" action="{set_all_url}" onsubmit="return confirm('Ro\'yxatdagi barcha o\'quvchilarga \'Biz bermadik\' belgilansinmi?');">
                  <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                  <button type="submit" class="btn btn-danger">Biz bermadik (Hammaga)</button>
                </form>
              </div>

              <div class="phone-counters mb-16">
                <span class="chip">Berildi: <strong>{phone_given_count}</strong></span>
                <span class="chip">Biz bermadik: <strong>{phone_not_given_by_us_count}</strong></span>
                <span class="chip">O'quvchi oldi: <strong>{student_took_count}</strong></span>
                <span class="chip">O'quvchi bermadi: <strong>{student_not_given_count}</strong></span>
              </div>

              <div id="camera-modal" class="camera-panel hidden">
                <div class="camera-header">
                  <strong id="camera-target-name">Kamera</strong>
                  <button type="button" class="btn btn-outline btn-sm" onclick="closeCamera()">Yopish</button>
                </div>
                <div class="camera-viewport">
                  <video id="camera-video" autoplay playsinline></video>
                  <canvas id="camera-canvas" class="hidden"></canvas>
                </div>
                <div id="camera-error" class="alert alert-error hidden"></div>
                <div class="camera-actions">
                  <button type="button" id="btn-capture-live" class="btn btn-primary full-width" onclick="capturePhoto()">Jonli Kamerada Rasmga Olish</button>
                  <button type="button" id="btn-open-file-cam" class="btn btn-soft full-width mt-12" onclick="triggerFileCamera()">Kamera / Galereyadan Rasmga Olish va Saqlash</button>
                  <input type="file" id="camera-file-input" accept="image/*" capture="environment" class="hidden" onchange="handleFileCapture(this)" />
                </div>
                <form id="photo-submit-form" method="POST" action="{save_photo_url}" class="hidden">
                  <input type="hidden" name="student_id" id="photo-student-id" />
                  <input type="hidden" name="phone_status" id="photo-phone-status" value="Berildi" />
                  <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                  <input type="hidden" name="photo_base64" id="photo-base64" />
                </form>
              </div>

              <div class="table-wrap">
                <table class="excel-table responsive-table">
                  <thead><tr><th>O'quvchi</th><th>Sinf / Xona</th><th>Holat</th><th>Amallar</th><th>Isbot rasm</th></tr></thead>
                  <tbody>{p_rows}</tbody>
                </table>
              </div>
            </section>
            """
        elif page_name == "students":
            add_st_url = url_for("add_student")
            room_select_opts = ""
            for rg in rooms_grouped:
                dis = "disabled" if rg["occupied_count"] >= rg["capacity"] else ""
                r_code = esc(rg["code"])
                r_sub = esc(rg["sub_label"])
                r_occ = rg["occupied_count"]
                r_cap = rg["capacity"]
                room_select_opts += f'<option value="{rg["id"]}" {dis}>{r_code} — {r_sub} ({r_occ}/{r_cap})</option>'
            st_rows = ""
            for st in students:
                del_url = url_for("delete_student", student_id=st["id"])
                st_rows += f"""
                <tr>
                  <td data-label="Ism-familiya">{esc(st["full_name"])}</td>
                  <td data-label="Sinf">{esc(st["class_name"])}</td>
                  <td data-label="Xona">{esc(st["room_code"])} ({esc(st["room_sub_label"])})</td>
                  <td data-label="Amal">
                    <form method="POST" action="{del_url}" onsubmit="return confirm('O\'chirilsinmi?');">
                      <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                      <button type="submit" class="btn btn-danger btn-sm">O'chirish</button>
                    </form>
                  </td>
                </tr>
                """
            sub_content = f"""
            <section class="glass-card section-box">
              <h2>O'quvchi Qo'shish va Ro'yxat</h2>
              <form method="POST" action="{add_st_url}" class="form-grid mb-16">
                <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                <div>
                  <label class="field-label">Ism-familiya</label>
                  <input type="text" name="full_name" class="input" required />
                </div>
                <div>
                  <label class="field-label">Sinf</label>
                  <input type="text" name="class_name" class="input" placeholder="Masalan: 9-A" required />
                </div>
                <div>
                  <label class="field-label">Xona (1c-12c)</label>
                  <select name="room_id" class="input" required>{room_select_opts}</select>
                </div>
                <div class="align-end">
                  <button type="submit" class="btn btn-primary full-width">Qo'shish</button>
                </div>
              </form>
              <div class="table-wrap">
                <table class="excel-table responsive-table">
                  <thead><tr><th>Ism-familiya</th><th>Sinf</th><th>Xona</th><th>Amal</th></tr></thead>
                  <tbody>{st_rows}</tbody>
                </table>
              </div>
            </section>
            """
        elif page_name == "rooms":
            r_cards = ""
            for rg in rooms_grouped:
                if rg["occupants"]:
                    lis = ""
                    for o in rg["occupants"]:
                        m_cls = "plus" if o["status"] == "+" else ("minus" if o["status"] == "-" else "wait")
                        m_txt = esc(o["status"]) or "?"
                        lis += f'<li><span>{esc(o["full_name"])} <small>({esc(o["class_name"])})</small></span><span class="mini-badge {m_cls}">{m_txt}</span></li>'
                    occ_html = f'<ul class="occupant-list">{lis}</ul>'
                else:
                    occ_html = '<p class="empty-room">Bo\'sh xona</p>'
                r_cards += f"""
                <div class="room-card">
                  <div class="room-card-top">
                    <span class="room-code-badge">{esc(rg["code"])}</span>
                    <span class="room-cap">{esc(rg["sub_label"])} &middot; <strong>{rg["occupied_count"]}/{rg["capacity"]}</strong></span>
                  </div>
                  <div class="room-present-note">Bor (+): <strong>{rg["present_in_room"]}</strong></div>
                  {occ_html}
                </div>
                """
            sub_content = f"""
            <section class="glass-card section-box">
              <h2>Xonalar (1c - 12c) &middot; Har birida 6 kishilik va 4 kishilik</h2>
              <div class="rooms-grid mt-12">{r_cards}</div>
            </section>
            """
        elif page_name == "superadmin" and role == "superadmin":
            chk = "checked" if auto_theme_enabled else ""
            upd_sup_url = url_for("superadmin_update_supervisor")
            tog_ontime_url = url_for("superadmin_toggle_ontime")
            tog_theme_url = url_for("superadmin_toggle_auto_theme")
            sup_cards_html = ""
            for sp in supervisors:
                duty_pill = '<span class="badge badge-phone">Hozir navbatchi</span>' if sp["on_duty"] else '<span class="badge badge-none">Navbatda emas</span>'
                ontime_pill = f'<span class="badge badge-plus">Ishiga o\'z vaqtida keldi ({esc(sp["arrived_at"])})</span>' if sp["on_time"] == 1 else '<span class="badge badge-none">Hali belgilanmagan</span>'
                sub_val = "0" if sp["on_time"] == 1 else "1"
                sub_lbl = "Belginini olish" if sp["on_time"] == 1 else "Ishiga o'z vaqtida keldi"
                sup_cards_html += f"""
                <div class="room-card">
                  <div class="room-card-top">
                    <span class="room-code-badge">{sp["slot_number"]}-Navbat</span>
                    {duty_pill}
                  </div>
                  <div class="mt-12">{ontime_pill}</div>
                  <form method="POST" action="{upd_sup_url}" class="mt-12">
                    <input type="hidden" name="slot_number" value="{sp["slot_number"]}" />
                    <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                    <label class="field-label">Ustoz ismi</label>
                    <input type="text" name="teacher_name" class="input input-sm" value="{esc(sp["teacher_name"])}" required />
                    <div class="form-grid mt-12">
                      <div>
                        <label class="field-label">Smena boshlanishi</label>
                        <input type="text" name="shift_start" class="input input-sm" value="{esc(sp["shift_start"])}" required />
                      </div>
                      <div>
                        <label class="field-label">Smena tugashi</label>
                        <input type="text" name="shift_end" class="input input-sm" value="{esc(sp["shift_end"])}" required />
                      </div>
                    </div>
                    <button type="submit" class="btn btn-primary btn-sm full-width mt-12">Saqlash</button>
                  </form>
                  <form method="POST" action="{tog_ontime_url}" class="mt-12">
                    <input type="hidden" name="slot_number" value="{sp["slot_number"]}" />
                    <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                    <input type="hidden" name="on_time" value="{sub_val}" />
                    <button type="submit" class="btn btn-soft btn-sm full-width">{sub_lbl}</button>
                  </form>
                </div>
                """
            sub_content = f"""
            <section class="glass-card section-box">
              <h2>Super Admin Boshqaruvi</h2>
              <div class="chip-row mt-12 mb-16">
                <span class="chip">Davomat yozuvlari: <strong>{total_attendance_records}</strong></span>
                <span class="chip">Kamera rasmlari: <strong>{total_photos}</strong></span>
              </div>
              <form method="POST" action="{tog_theme_url}" class="mb-16">
                <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                <label class="checkbox-inline">
                  <input type="checkbox" name="auto_theme" value="1" {chk} onchange="this.form.submit()" />
                  <span>Soatga qarab avtomatik Kunduzgi / Kechki rejim</span>
                </label>
              </form>
              <div class="rooms-grid">{sup_cards_html}</div>
            </section>
            """

        date_form_url = url_for(endpoint_name)
        set_theme_url = url_for("set_theme")
        set_device_url = url_for("set_device")
        att_nav_url = url_for("attendance_page", date=selected_date)
        phones_nav_url = url_for("phones_page", date=selected_date)
        students_nav_url = url_for("students_page", date=selected_date)
        rooms_nav_url = url_for("rooms_page", date=selected_date)
        chef_nav_url = url_for("chef_page", date=selected_date)

        att_act = "active" if page_name == "attendance" else ""
        ph_act = "active" if page_name == "phones" else ""
        st_act = "active" if page_name == "students" else ""
        rm_act = "active" if page_name == "rooms" else ""
        ch_act = "active" if page_name == "chef" else ""

        body_html = f"""
        <header class="topbar glass-card">
          <div class="brand-area">
            {LOGO_BADGE_HTML}
            <div>
              <div class="brand-title">Yotoqxona Nazorati</div>
              <div class="brand-sub">
                {esc(format_uz_date(selected_date))} &middot; Soat: <strong id="uz-live-clock">{esc(now_clock_str)}</strong> (UTC+5) &middot; Navbatchi: <strong>{duty_banner_text}</strong>
              </div>
            </div>
          </div>
          <div class="top-actions">
            <form method="GET" action="{date_form_url}" class="inline-form date-form">
              <input type="date" name="date" value="{esc(selected_date)}" class="input input-sm" onchange="this.form.submit()" />
            </form>
            {today_btn}
            <form method="POST" action="{set_theme_url}" class="inline-form">
              <input type="hidden" name="theme" value="{theme_next_val}" />
              <input type="hidden" name="next_url" value="{esc(request.full_path)}" />
              <button type="submit" class="btn btn-outline btn-sm">{theme_btn_label}</button>
            </form>
            <form method="POST" action="{set_device_url}" class="inline-form">
              <input type="hidden" name="device_mode" value="{device_next_val}" />
              <input type="hidden" name="next_url" value="{esc(request.full_path)}" />
              <button type="submit" class="btn btn-outline btn-sm">{device_btn_label}</button>
            </form>
            {role_badge_html}
          </div>
        </header>

        {other_date_banner}

        <nav class="nav-tabs glass-card">
          <a href="{att_nav_url}" class="nav-tab {att_act}">1. Davomat</a>
          <a href="{phones_nav_url}" class="nav-tab {ph_act}">2. Telefon</a>
          <a href="{students_nav_url}" class="nav-tab {st_act}">3. O'quvchilar</a>
          <a href="{rooms_nav_url}" class="nav-tab {rm_act}">4. Xonalar</a>
          <a href="{chef_nav_url}" class="nav-tab {ch_act}">Oshpaz</a>
          {super_tab}
        </nav>

        <section class="stats-strip">
          <div class="stat-tile glass-card"><span>Jami</span><strong id="stat-total">{total_count}</strong></div>
          <div class="stat-tile glass-card"><span>Bor (+)</span><strong id="stat-present">{present_count}</strong></div>
          <div class="stat-tile glass-card"><span>Yo'q (-)</span><strong id="stat-absent">{absent_count}</strong></div>
          <div class="stat-tile glass-card"><span>Belgilanmagan</span><strong id="stat-unmarked">{unmarked_count}</strong></div>
        </section>

        {sub_content}
        """

    bulk_api_url = url_for("api_attendance_bulk")
    single_api_url = url_for("api_attendance")

    return f"""<!DOCTYPE html>
<html lang="uz" data-theme="{esc(theme)}" data-device="{esc(device_mode)}">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Yotoqxona Nazorati</title>
  <link rel="icon" type="image/svg+xml" href="{FAVICON_DATA_URI}" />
  <style>
    :root {{
      --bg: #eef2f7;
      --card: rgba(255, 255, 255, 0.92);
      --input-bg: #ffffff;
      --text: #0f172a;
      --muted: #64748b;
      --border: rgba(15, 23, 42, 0.14);
      --grid-border: rgba(15, 23, 42, 0.18);
      --primary: #2563eb;
      --danger: #dc2626;
      --ok: #16a34a;
      --warn: #d97706;
    }}
    html[data-theme="day"] {{
      color-scheme: light;
    }}
    html[data-theme="night"] {{
      color-scheme: dark;
      --bg: #0b1120;
      --card: rgba(15, 23, 42, 0.92);
      --input-bg: #111827;
      --text: #f8fafc;
      --muted: #94a3b8;
      --border: rgba(148, 163, 184, 0.24);
      --grid-border: rgba(148, 163, 184, 0.28);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 16px;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    .glass-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 16px;
      backdrop-filter: blur(10px);
    }}
    .topbar {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .brand-area {{ display: flex; align-items: center; gap: 12px; }}
    .brand-logo {{
      width: 42px; height: 42px; border-radius: 14px; overflow: hidden;
      display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;
      box-shadow: 0 6px 16px rgba(37, 99, 235, 0.22);
    }}
    .brand-logo svg {{ width: 100%; height: 100%; display: block; }}
    .brand-title {{ font-size: 20px; font-weight: 700; }}
    .brand-sub, .muted {{ color: var(--muted); font-size: 13px; }}
    .top-actions {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }}
    .inline-form {{ display: inline-flex; margin: 0; }}
    .nav-tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }}
    .nav-tab {{
      text-decoration: none;
      color: var(--text);
      padding: 8px 14px;
      border-radius: 10px;
      font-weight: 600;
      font-size: 14px;
    }}
    .nav-tab.active {{ background: var(--primary); color: #fff; }}
    .stats-strip {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .stat-tile {{ display: flex; justify-content: space-between; align-items: center; }}
    .stat-tile strong {{ font-size: 20px; }}
    .section-header {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .filter-bar {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    .table-wrap {{ overflow-x: auto; }}
    .excel-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    .excel-table th, .excel-table td {{
      padding: 10px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: middle;
    }}
    .excel-grid-table {{
      border: 1px solid var(--grid-border);
    }}
    .excel-grid-table th {{
      background: rgba(37, 99, 235, 0.08);
      border: 1px solid var(--grid-border);
      font-weight: 700;
    }}
    .excel-grid-table td {{
      border: 1px solid var(--grid-border);
    }}
    .excel-row.active-row {{ outline: 2px solid var(--primary); background: rgba(37, 99, 235, 0.07); }}
    .excel-row.needs-attention {{ outline: 2px solid var(--danger); background: rgba(220, 38, 38, 0.10); }}
    .att-actions {{ display: flex; align-items: center; gap: 6px; }}
    .cell-btn {{
      width: 34px; height: 34px; border-radius: 9px; border: 1px solid var(--border);
      background: var(--input-bg); color: var(--text); font-weight: 700; cursor: pointer;
    }}
    .cell-btn.is-plus {{ background: var(--ok); color: #fff; border-color: var(--ok); }}
    .cell-btn.is-minus {{ background: var(--danger); color: #fff; border-color: var(--danger); }}
    .status-tag {{ margin-left: 4px; font-weight: 600; font-size: 12px; }}
    .inline-reason-editor {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }}
    .hidden {{ display: none !important; }}
    .input {{
      padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border);
      background: var(--input-bg); color: var(--text); font-size: 14px;
    }}
    select.input, select.input option, select.input optgroup {{
      background-color: var(--input-bg);
      color: var(--text);
    }}
    html[data-theme="night"] select.input,
    html[data-theme="night"] select.input option,
    html[data-theme="night"] select.input optgroup {{
      background-color: #111827;
      color: #f8fafc;
    }}
    html[data-theme="day"] select.input,
    html[data-theme="day"] select.input option,
    html[data-theme="day"] select.input optgroup {{
      background-color: #ffffff;
      color: #0f172a;
    }}
    .input-sm {{ padding: 6px 8px; font-size: 13px; }}
    .btn {{
      padding: 9px 14px; border-radius: 10px; border: 1px solid transparent;
      font-weight: 600; font-size: 14px; cursor: pointer; text-decoration: none; display: inline-flex;
      align-items: center; justify-content: center; gap: 6px;
    }}
    .btn-sm {{ padding: 6px 11px; font-size: 12px; }}
    .btn-primary {{ background: var(--primary); color: #fff; }}
    .btn-danger {{ background: var(--danger); color: #fff; }}
    .btn-soft {{ background: rgba(37, 99, 235, 0.14); color: var(--primary); }}
    html[data-theme="night"] .btn-soft {{ color: #93c5fd; background: rgba(59, 130, 246, 0.2); }}
    .btn-outline {{ border-color: var(--border); background: var(--input-bg); color: var(--text); }}
    .full-width {{ width: 100%; }}
    .mt-12 {{ margin-top: 12px; }}
    .mb-16 {{ margin-bottom: 16px; }}
    .chip-row, .phone-counters, .phone-btn-group {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .chip {{ padding: 6px 10px; border-radius: 999px; border: 1px solid var(--border); font-size: 13px; background: var(--input-bg); }}
    .badge {{ padding: 4px 8px; border-radius: 8px; font-size: 12px; font-weight: 600; }}
    .badge-phone {{ background: rgba(22, 163, 74, 0.15); color: var(--ok); }}
    .badge-taken {{ background: rgba(37, 99, 235, 0.15); color: var(--primary); }}
    .badge-notgiven {{ background: rgba(217, 119, 6, 0.15); color: var(--warn); }}
    .badge-plus {{ background: rgba(22, 163, 74, 0.15); color: var(--ok); }}
    .badge-none {{ background: rgba(148, 163, 184, 0.18); color: var(--muted); }}
    .role-pill {{ padding: 5px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; background: rgba(37, 99, 235, 0.15); }}
    .alert {{ padding: 10px 14px; border-radius: 10px; margin-bottom: 12px; font-weight: 600; }}
    .alert-error {{ background: rgba(220, 38, 38, 0.15); color: var(--danger); border: 1px solid rgba(220, 38, 38, 0.35); }}
    .alert-success {{ background: rgba(22, 163, 74, 0.15); color: var(--ok); border: 1px solid rgba(22, 163, 74, 0.35); }}
    .alert-warn {{ background: rgba(217, 119, 6, 0.16); color: var(--warn); border: 1px solid rgba(217, 119, 6, 0.35); }}
    .bulk-save-bar {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
    }}
    .bulk-save-actions {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .login-wrapper {{ display: flex; justify-content: center; padding: 32px 0; }}
    .login-card {{ width: 100%; max-width: 420px; }}
    .login-brand {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }}
    .login-brand h1 {{ margin: 0 0 2px; font-size: 22px; }}
    .field-label {{ display: block; font-size: 13px; font-weight: 600; margin-bottom: 4px; }}
    .role-select-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 12px; }}
    .role-option {{
      border: 1px solid var(--border); border-radius: 10px; padding: 8px;
      display: flex; gap: 6px; align-items: flex-start; cursor: pointer; background: var(--input-bg);
    }}
    .role-option span {{ display: flex; flex-direction: column; font-size: 12px; }}
    .chef-section {{ text-align: center; max-width: 680px; margin: 20px auto; }}
    .chef-big-number {{ font-size: 56px; font-weight: 800; color: var(--primary); margin: 8px 0; }}
    .chef-big-number span {{ font-size: 20px; font-weight: 600; }}
    .chef-substats {{ display: flex; justify-content: center; gap: 20px; margin-bottom: 16px; }}
    .form-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }}
    .align-end {{ display: flex; align-items: flex-end; }}
    .rooms-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 12px; }}
    .room-card {{ border: 1px solid var(--border); border-radius: 12px; padding: 12px; background: var(--input-bg); }}
    .room-card-top {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
    .room-code-badge {{ font-weight: 800; font-size: 15px; }}
    .occupant-list {{ list-style: none; padding: 0; margin: 8px 0 0; }}
    .occupant-list li {{ display: flex; justify-content: space-between; padding: 4px 0; border-top: 1px dashed var(--border); font-size: 13px; }}
    .mini-badge.plus {{ color: var(--ok); font-weight: 700; }}
    .mini-badge.minus {{ color: var(--danger); font-weight: 700; }}
    .camera-panel {{ border: 1px solid var(--border); border-radius: 14px; padding: 14px; margin-bottom: 16px; background: rgba(37, 99, 235, 0.05); }}
    .camera-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
    .camera-viewport video {{ width: 100%; max-height: 320px; border-radius: 12px; background: #000; }}
    .checkbox-inline {{ display: inline-flex; align-items: center; gap: 8px; cursor: pointer; }}

    html[data-device="phone"] .container {{ max-width: 460px; }}
    html[data-device="phone"] body {{ padding: 7px; font-size: 13px; }}
    html[data-device="phone"] .glass-card {{ padding: 9px; border-radius: 11px; }}
    html[data-device="phone"] .brand-logo {{ width: 32px; height: 32px; border-radius: 10px; }}
    html[data-device="phone"] .brand-title {{ font-size: 15px; }}
    html[data-device="phone"] .brand-sub,
    html[data-device="phone"] .muted {{ font-size: 11px; }}
    html[data-device="phone"] .topbar {{ gap: 7px; margin-bottom: 8px; }}
    html[data-device="phone"] .top-actions {{ width: 100%; gap: 5px; }}
    html[data-device="phone"] .top-actions .btn,
    html[data-device="phone"] .top-actions .input {{ flex: 1 1 auto; padding: 5px 8px; font-size: 11px; border-radius: 8px; }}
    html[data-device="phone"] .nav-tabs {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; padding: 6px; margin-bottom: 8px; }}
    html[data-device="phone"] .nav-tab {{ text-align: center; padding: 6px 5px; font-size: 11px; border-radius: 8px; }}
    html[data-device="phone"] .stats-strip {{ grid-template-columns: repeat(4, 1fr); gap: 5px; margin-bottom: 8px; }}
    html[data-device="phone"] .stat-tile {{ flex-direction: column; align-items: flex-start; padding: 6px 8px; border-radius: 9px; }}
    html[data-device="phone"] .stat-tile span {{ font-size: 10px; color: var(--muted); }}
    html[data-device="phone"] .stat-tile strong {{ font-size: 15px; }}
    html[data-device="phone"] .section-header {{ gap: 6px; margin-bottom: 8px; }}
    html[data-device="phone"] .section-header h2 {{ font-size: 15px; margin: 0 0 2px; }}
    html[data-device="phone"] .btn {{ padding: 6px 9px; font-size: 11px; border-radius: 8px; }}
    html[data-device="phone"] .btn-sm {{ padding: 5px 8px; font-size: 11px; }}
    html[data-device="phone"] .input {{ padding: 6px 8px; font-size: 12px; border-radius: 8px; }}
    html[data-device="phone"] .input-sm {{ padding: 5px 7px; font-size: 11px; }}
    html[data-device="phone"] .cell-btn {{ width: 32px; height: 32px; font-size: 14px; border-radius: 8px; }}
    html[data-device="phone"] .status-tag {{ font-size: 11px; }}
    html[data-device="phone"] .chip {{ padding: 4px 8px; font-size: 11px; }}
    html[data-device="phone"] .phone-btn-group {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 5px; width: 100%; }}
    html[data-device="phone"] .phone-btn-group .inline-form {{ width: 100%; }}
    html[data-device="phone"] .phone-btn-group .inline-form .btn,
    html[data-device="phone"] .phone-btn-group .btn {{ width: 100%; min-height: 30px; padding: 5px 6px; font-size: 11px; }}
    html[data-device="phone"] .responsive-table thead {{ display: none; }}
    html[data-device="phone"] .excel-grid-table {{ border: none; }}
    html[data-device="phone"] .responsive-table tr {{
      display: block; border: 1px solid var(--grid-border); border-radius: 10px;
      padding: 7px 8px; margin-bottom: 7px; background: var(--input-bg);
    }}
    html[data-device="phone"] .responsive-table td {{
      display: flex; justify-content: space-between; align-items: center; gap: 8px;
      border: none; border-bottom: 1px dashed var(--border); padding: 4px 2px; font-size: 12px;
    }}
    html[data-device="phone"] .responsive-table td:last-child {{ border-bottom: none; }}
    html[data-device="phone"] .responsive-table td::before {{
      content: attr(data-label); font-weight: 700; color: var(--muted); font-size: 11px; flex-shrink: 0;
    }}
    html[data-device="phone"] .bulk-save-bar {{ flex-direction: column; align-items: stretch; gap: 8px; margin-top: 8px; padding-top: 8px; }}
    html[data-device="phone"] .bulk-save-actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }}

    @media (max-width: 720px) {{
      .container {{ max-width: 480px; }}
      body {{ padding: 8px; font-size: 13px; }}
      .glass-card {{ padding: 10px; border-radius: 12px; }}
      .topbar {{ flex-direction: column; align-items: stretch; gap: 8px; }}
      .nav-tabs {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; padding: 6px; }}
      .nav-tab {{ text-align: center; padding: 7px 6px; font-size: 12px; }}
      .stats-strip {{ grid-template-columns: repeat(2, 1fr); gap: 6px; }}
      .cell-btn {{ width: 34px; height: 34px; font-size: 14px; }}
      .phone-btn-group {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 5px; width: 100%; }}
      .phone-btn-group .inline-form {{ width: 100%; }}
      .phone-btn-group .inline-form .btn,
      .phone-btn-group .btn {{ width: 100%; min-height: 32px; font-size: 11px; }}
    }}
  </style>
</head>
<body>
  <main class="container">
    {flash_html}
    {body_html}
  </main>
  <script>
    const CURRENT_DATE = "{esc(selected_date)}";
    const BULK_API_URL = "{bulk_api_url}";
    const SINGLE_API_URL = "{single_api_url}";
    let cameraStream = null;

    function tickUzClock() {{
      const el = document.getElementById("uz-live-clock");
      if (!el) return;
      const now = new Date();
      const uzMs = now.getTime() + (now.getTimezoneOffset() + 300) * 60000;
      const d = new Date(uzMs);
      const hh = String(d.getHours()).padStart(2, "0");
      const mm = String(d.getMinutes()).padStart(2, "0");
      const ss = String(d.getSeconds()).padStart(2, "0");
      el.textContent = `${{hh}}:${{mm}}:${{ss}}`;
    }}
    tickUzClock();
    setInterval(tickUzClock, 1000);

    function refreshStatsFromTable() {{
      const rows = Array.from(document.querySelectorAll("#excel-attendance-table .excel-row"));
      if (!rows.length) return;
      let p = 0, a = 0, u = 0;
      for (const r of rows) {{
        const st = (r.dataset.status || "").trim();
        if (st === "+") p += 1;
        else if (st === "-") a += 1;
        else u += 1;
      }}
      const elP = document.getElementById("stat-present");
      const elA = document.getElementById("stat-absent");
      const elU = document.getElementById("stat-unmarked");
      if (elP) elP.textContent = String(p);
      if (elA) elA.textContent = String(a);
      if (elU) elU.textContent = String(u);
    }}

    function refreshUnmarkedSummary() {{
      const summary = document.getElementById("att-unmarked-summary");
      if (!summary) return;
      const rows = Array.from(document.querySelectorAll("#excel-attendance-table .excel-row"));
      const missing = rows.filter(r => !["+", "-"].includes((r.dataset.status || "").trim()));
      if (!rows.length) {{
        summary.textContent = "O'quvchilar ro'yxati bo'sh.";
      }} else if (missing.length === 0) {{
        summary.textContent = `Barcha ${{rows.length}} nafar o'quvchi belgilangan (${{CURRENT_DATE}}).`;
      }} else {{
        summary.textContent = `Belgilanmagan: ${{missing.length}} nafar (masalan: ${{missing[0].dataset.studentName || ""}})`;
      }}
    }}

    function showAttendanceFeedback(type, message) {{
      const box = document.getElementById("att-save-feedback");
      if (!box) return;
      box.className = `alert alert-${{type}}`;
      box.textContent = message;
      box.classList.remove("hidden");
    }}

    function focusRow(row) {{
      if (!row) return;
      document.querySelectorAll(".excel-row").forEach(r => r.classList.remove("active-row"));
      row.classList.add("active-row");
      row.focus();
    }}

    function moveRow(delta) {{
      const rows = Array.from(document.querySelectorAll(".excel-row"));
      if (!rows.length) return;
      let idx = rows.findIndex(r => r.classList.contains("active-row"));
      if (idx === -1) idx = 0;
      const nextIdx = Math.min(rows.length - 1, Math.max(0, idx + delta));
      focusRow(rows[nextIdx]);
    }}

    async function persistSingleAttendance(sid, status, reasonType, reasonNote) {{
      try {{
        const res = await fetch(SINGLE_API_URL, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            student_id: sid,
            work_date: CURRENT_DATE,
            status: status,
            reason_type: reasonType || "",
            reason_note: reasonNote || ""
          }})
        }});
        if (!res.ok) {{
          showAttendanceFeedback("error", "Bazaga yozishda xatolik yuz berdi.");
        }}
      }} catch (e) {{
        showAttendanceFeedback("error", "Internet yoki server bilan bog'lanishda xatolik.");
      }}
    }}

    function setRowPlus(row) {{
      if (!row) return;
      const sid = row.dataset.studentId;
      row.dataset.status = "+";
      row.classList.remove("needs-attention");
      row.querySelector(".plus-btn")?.classList.add("is-plus");
      row.querySelector(".minus-btn")?.classList.remove("is-minus");
      const tag = document.getElementById("tag-" + sid);
      if (tag) tag.textContent = "Bor (+)";
      const rdisp = document.getElementById("reason-display-" + sid);
      if (rdisp) rdisp.textContent = "";
      document.getElementById("editor-" + sid)?.classList.add("hidden");
      refreshStatsFromTable();
      refreshUnmarkedSummary();
      persistSingleAttendance(sid, "+", "", "");
      moveRow(1);
    }}

    function openRowMinus(row) {{
      if (!row) return;
      focusRow(row);
      const sid = row.dataset.studentId;
      document.getElementById("editor-" + sid)?.classList.remove("hidden");
      checkOtherInput(sid);
    }}

    function checkOtherInput(sid) {{
      const sel = document.getElementById("rtype-" + sid);
      const inp = document.getElementById("rnote-" + sid);
      if (!sel || !inp) return;
      if (sel.value === "Boshqa sabab") {{
        inp.classList.remove("hidden");
      }} else {{
        inp.classList.add("hidden");
      }}
    }}

    function saveRowMinus(sid, row) {{
      const rtype = document.getElementById("rtype-" + sid)?.value || "Sababli";
      const rnote = document.getElementById("rnote-" + sid)?.value || "";
      if (row) {{
        row.dataset.status = "-";
        row.classList.remove("needs-attention");
        row.querySelector(".minus-btn")?.classList.add("is-minus");
        row.querySelector(".plus-btn")?.classList.remove("is-plus");
      }}
      const tag = document.getElementById("tag-" + sid);
      if (tag) tag.textContent = "Yo'q (-)";
      const rdisp = document.getElementById("reason-display-" + sid);
      if (rdisp) rdisp.textContent = rnote ? `${{rtype}}: ${{rnote}}` : rtype;
      document.getElementById("editor-" + sid)?.classList.add("hidden");
      refreshStatsFromTable();
      refreshUnmarkedSummary();
      persistSingleAttendance(sid, "-", rtype, rnote);
      moveRow(1);
    }}

    async function saveAllAttendance() {{
      const rows = Array.from(document.querySelectorAll("#excel-attendance-table .excel-row"));
      if (!rows.length) return;

      rows.forEach(r => r.classList.remove("needs-attention"));

      const items = [];
      for (const row of rows) {{
        const sid = row.dataset.studentId;
        const sname = row.dataset.studentName || "O'quvchi";
        const status = (row.dataset.status || "").trim();
        if (status !== "+" && status !== "-") {{
          row.classList.add("needs-attention");
          focusRow(row);
          row.scrollIntoView({{ behavior: "smooth", block: "center" }});
          showAttendanceFeedback("error", `Avval shu insonni davomatini oling, so'ng saqlaysiz: ${{sname}}`);
          return;
        }}
        const rtype = status === "-" ? (document.getElementById("rtype-" + sid)?.value || "Sababli") : "";
        const rnote = status === "-" ? (document.getElementById("rnote-" + sid)?.value || "") : "";
        items.push({{
          student_id: sid,
          status: status,
          reason_type: rtype,
          reason_note: rnote
        }});
      }}

      const btn = document.getElementById("btn-save-all-attendance");
      if (btn) btn.disabled = true;

      try {{
        const res = await fetch(BULK_API_URL, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ work_date: CURRENT_DATE, items: items }})
        }});
        const data = await res.json().catch(() => ({{}}));
        if (res.ok && data.ok) {{
          refreshStatsFromTable();
          refreshUnmarkedSummary();
          showAttendanceFeedback("success", `${{items.length}} nafar o'quvchi davomati (${{CURRENT_DATE}}) bazaga saqlandi.`);
        }} else {{
          showAttendanceFeedback("error", data.error || "Saqlashda xatolik yuz berdi.");
        }}
      }} catch (err) {{
        showAttendanceFeedback("error", "Internet yoki server bilan bog'lanishda xatolik.");
      }} finally {{
        if (btn) btn.disabled = false;
      }}
    }}

    refreshUnmarkedSummary();

    document.addEventListener("keydown", (e) => {{
      if (!document.getElementById("excel-attendance-table")) return;
      const activeTag = document.activeElement?.tagName?.toLowerCase();
      if (activeTag === "input" || activeTag === "select" || activeTag === "textarea") return;
      const curRow = document.querySelector(".excel-row.active-row") || document.querySelector(".excel-row");
      if (e.key === "ArrowDown") {{ e.preventDefault(); moveRow(1); }}
      else if (e.key === "ArrowUp") {{ e.preventDefault(); moveRow(-1); }}
      else if (e.key === "+" || e.key === "=") {{ e.preventDefault(); setRowPlus(curRow); }}
      else if (e.key === "-") {{ e.preventDefault(); openRowMinus(curRow); }}
    }});

    function openCameraFromBtn(btn) {{
      if (!btn) return;
      const sid = btn.getAttribute("data-student-id");
      const sname = btn.getAttribute("data-student-name") || "";
      const pstatus = btn.getAttribute("data-phone-status") || "Berildi";
      openLiveCamera(sid, sname, pstatus);
    }}

    function triggerFileCamera() {{
      const fileInput = document.getElementById("camera-file-input");
      if (fileInput) fileInput.click();
    }}

    async function openLiveCamera(studentId, studentName, phoneStatus) {{
      const modal = document.getElementById("camera-modal");
      const errBox = document.getElementById("camera-error");
      const video = document.getElementById("camera-video");
      const captureBtn = document.getElementById("btn-capture-live");
      const fileInput = document.getElementById("camera-file-input");
      if (!modal || !video) return;

      closeCamera();
      document.getElementById("camera-target-name").textContent = `${{studentName}} — ${{phoneStatus}}`;
      document.getElementById("photo-student-id").value = studentId;
      document.getElementById("photo-phone-status").value = phoneStatus;
      if (fileInput) fileInput.value = "";
      errBox?.classList.add("hidden");
      if (captureBtn) captureBtn.classList.remove("hidden");
      modal.classList.remove("hidden");
      modal.scrollIntoView({{ behavior: "smooth", block: "nearest" }});

      const isSecure = window.isSecureContext || location.hostname === "localhost" || location.hostname === "127.0.0.1";
      if (!isSecure || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {{
        if (captureBtn) captureBtn.classList.add("hidden");
        if (errBox) {{
          errBox.textContent = "Jonli kamera faqat HTTPS yoki localhost da ishlaydi. 'Kamera / Galereyadan Rasmga Olish va Saqlash' tugmasini bosing.";
          errBox.classList.remove("hidden");
        }}
        return;
      }}

      try {{
        cameraStream = await navigator.mediaDevices.getUserMedia({{
          video: {{ facingMode: {{ ideal: "environment" }} }},
          audio: false
        }});
        video.srcObject = cameraStream;
        await video.play();
      }} catch (err) {{
        if (captureBtn) captureBtn.classList.add("hidden");
        if (errBox) {{
          errBox.textContent = "Kamera ochilmadi (" + (err.name || err.message || err) + "). 'Kamera / Galereyadan Rasmga Olish va Saqlash' tugmasini bosing.";
          errBox.classList.remove("hidden");
        }}
      }}
    }}

    function closeCamera() {{
      if (cameraStream) {{
        cameraStream.getTracks().forEach(t => t.stop());
        cameraStream = null;
      }}
      const video = document.getElementById("camera-video");
      if (video) video.srcObject = null;
      document.getElementById("camera-modal")?.classList.add("hidden");
    }}

    function capturePhoto() {{
      const video = document.getElementById("camera-video");
      const canvas = document.getElementById("camera-canvas");
      if (!video || !canvas) return;
      canvas.width = video.videoWidth || 640;
      canvas.height = video.videoHeight || 480;
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      document.getElementById("photo-base64").value = canvas.toDataURL("image/jpeg", 0.85);
      closeCamera();
      document.getElementById("photo-submit-form")?.submit();
    }}

    function handleFileCapture(input) {{
      const file = input.files && input.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = function(ev) {{
        const img = new Image();
        img.onload = function() {{
          const canvas = document.getElementById("camera-canvas");
          if (!canvas) return;
          const maxW = 960;
          const scale = img.width > maxW ? (maxW / img.width) : 1;
          canvas.width = Math.round(img.width * scale) || 640;
          canvas.height = Math.round(img.height * scale) || 480;
          canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
          document.getElementById("photo-base64").value = canvas.toDataURL("image/jpeg", 0.85);
          closeCamera();
          document.getElementById("photo-submit-form")?.submit();
        }};
        img.src = ev.target.result;
      }};
      reader.readAsDataURL(file);
    }}
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    role = current_role()
    if role:
        return redirect(role_home_url(role))
    return redirect(url_for("login_page"))


@app.route("/login", methods=["GET"])
def login_page():
    if current_role():
        return redirect(role_home_url(current_role()))
    return render_page("login")


@app.route("/login", methods=["POST"])
def login():
    ip = get_client_ip()
    locked, rem = is_login_locked(ip)
    if locked:
        flash(f"Ko'p xato urinish. {rem} soniyadan so'ng qayta urining.", "error")
        return redirect(url_for("login_page"))

    role = (request.form.get("role") or "").strip()
    pin = (request.form.get("pin") or "").strip()
    if role in PINS and pin == PINS[role]:
        clear_failed_login(ip)
        session["role"] = role
        flash("Tizimga muvaffaqiyatli kirdingiz.", "success")
        return redirect(role_home_url(role))

    record_failed_login(ip)
    flash("Rol yoki PIN-kod noto'g'ri.", "error")
    return redirect(url_for("login_page"))


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("role", None)
    flash("Tizimdan chiqdingiz.", "success")
    return redirect(url_for("login_page"))


@app.route("/attendance")
def attendance_page():
    guard = require_login(["ustoz", "superadmin"])
    if guard:
        return guard
    return render_page("attendance")


@app.route("/attendance/export.xlsx")
@app.route("/attendance/export.xls")
def export_attendance_excel_xlsx():
    guard = require_login(["ustoz", "superadmin"])
    if guard:
        return guard
    ensure_day_rollover()
    db = get_db()
    selected_date = request.args.get("date") or today_str()
    try:
        datetime.strptime(selected_date, "%Y-%m-%d")
    except ValueError:
        selected_date = today_str()

    class_filter = request.args.get("class_name", "ALL")
    room_code_filter = request.args.get("room_code", "ALL")

    students = fetch_students_with_attendance(db, selected_date)
    if class_filter != "ALL":
        students = [st for st in students if st["class_name"] == class_filter]
    if room_code_filter != "ALL":
        students = [st for st in students if st["room_code"] == room_code_filter]

    present_c = sum(1 for st in students if st["status"] == "+")
    absent_c = sum(1 for st in students if st["status"] == "-")
    unmarked_c = len(students) - present_c - absent_c

    headers = ["#", "Ism-familiya", "Sinf", "Xona", "Davomat", "Sabab", "Telefon holati"]
    col_widths = [6, 28, 12, 16, 15, 24, 24]

    rows_data = []
    for i, st in enumerate(students, start=1):
        st_label = "Bor (+)" if st["status"] == "+" else ("Yo'q (-)" if st["status"] == "-" else "Belgilanmagan")
        reason_str = ""
        if st["status"] == "-":
            reason_str = f"{st['reason_type']}: {st['reason_note']}" if st["reason_note"] else st["reason_type"]
        phone_str = st["phone_status"] or "Belgilanmagan"
        if st["phone_time"]:
            phone_str = f"{phone_str} ({st['phone_time']})"
        rows_data.append([
            i,
            st["full_name"],
            st["class_name"],
            f"{st['room_code']} ({st['room_capacity']} k.)",
            st_label,
            reason_str,
            phone_str,
        ])

    title_line = f"Yotoqxona Davomat Hisoboti — {format_uz_date(selected_date)}"
    summary_line = f"Jami: {len(students)} | Bor (+): {present_c} | Yo'q (-): {absent_c} | Belgilanmagan: {unmarked_c}"
    xlsx_bytes = build_xlsx_bytes(title_line, summary_line, headers, rows_data, col_widths, center_cols=[1, 3, 4, 5])

    fname = f"davomat_{selected_date}.xlsx"
    return Response(
        xlsx_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"; filename*=UTF-8\'\'{quote(fname)}',
            "Cache-Control": "no-store",
        },
    )


@app.route("/attendance/export-monthly.xlsx")
def export_attendance_monthly_xlsx():
    guard = require_login(["ustoz", "superadmin"])
    if guard:
        return guard
    ensure_day_rollover()
    db = get_db()

    month_raw = (request.args.get("month") or today_str()[:7]).strip()
    try:
        ym_dt = datetime.strptime(month_raw, "%Y-%m")
    except ValueError:
        ym_dt = datetime.strptime(today_str()[:7], "%Y-%m")

    year_num = ym_dt.year
    month_num = ym_dt.month
    days_in_month = calendar.monthrange(year_num, month_num)[1]
    month_prefix = f"{year_num:04d}-{month_num:02d}"

    class_filter = request.args.get("class_name", "ALL")
    room_code_filter = request.args.get("room_code", "ALL")

    sql = """
        SELECT s.id, s.full_name, s.class_name, r.code AS room_code, r.capacity AS room_capacity
        FROM students s
        JOIN rooms r ON s.room_id = r.id
        WHERE 1=1
    """
    params = []
    if class_filter != "ALL":
        sql += " AND s.class_name = ?"
        params.append(class_filter)
    if room_code_filter != "ALL":
        sql += " AND r.code = ?"
        params.append(room_code_filter)
    sql += " ORDER BY CAST(substr(r.code, 1, length(r.code)-1) AS INTEGER), r.capacity DESC, s.full_name"

    students_rows = db.execute(sql, params).fetchall()

    att_rows = db.execute("""
        SELECT student_id, work_date, COALESCE(status, '') AS status, COALESCE(phone_status, '') AS phone_status
        FROM attendance
        WHERE work_date LIKE ?
    """, (f"{month_prefix}-%",)).fetchall()

    status_map = {}
    latest_phone_map = {}
    for ar in att_rows:
        sid = ar["student_id"]
        wdate = ar["work_date"]
        status_map[(sid, wdate)] = ar["status"]
        ps = normalize_phone_status(ar["phone_status"])
        if ps:
            prev = latest_phone_map.get(sid)
            if not prev or wdate > prev[0]:
                latest_phone_map[sid] = (wdate, ps)

    day_cols = [f"{month_prefix}-{d:02d}" for d in range(1, days_in_month + 1)]
    headers = (
        ["#", "Ism-familiya", "Sinf", "Xona"]
        + [str(d) for d in range(1, days_in_month + 1)]
        + ["Bor (+)", "Yo'q (-)", "Telefon"]
    )
    col_widths = (
        [5, 26, 10, 14]
        + [4.5 for _ in range(days_in_month)]
        + [10, 10, 20]
    )
    center_cols = [1, 3, 4] + list(range(5, 5 + days_in_month)) + [5 + days_in_month, 6 + days_in_month]

    rows_data = []
    total_plus_all = 0
    total_minus_all = 0
    for idx, st in enumerate(students_rows, start=1):
        sid = st["id"]
        day_cells = []
        p_cnt = 0
        m_cnt = 0
        for d_iso in day_cols:
            stt = status_map.get((sid, d_iso), "")
            if stt == "+":
                p_cnt += 1
                day_cells.append("+")
            elif stt == "-":
                m_cnt += 1
                day_cells.append("-")
            else:
                day_cells.append("")
        total_plus_all += p_cnt
        total_minus_all += m_cnt
        phone_val = latest_phone_map[sid][1] if sid in latest_phone_map else "Belgilanmagan"
        rows_data.append(
            [idx, st["full_name"], st["class_name"], f"{st['room_code']} ({st['room_capacity']} k.)"]
            + day_cells
            + [p_cnt, m_cnt, phone_val]
        )

    title_line = f"Yotoqxona 1 Oylik Davomat Hisoboti — {format_uz_month(year_num, month_num)}"
    summary_line = f"O'quvchilar: {len(students_rows)} | Kunlar: {days_in_month} | Jami Bor (+): {total_plus_all} | Jami Yo'q (-): {total_minus_all}"
    xlsx_bytes = build_xlsx_bytes(title_line, summary_line, headers, rows_data, col_widths, center_cols=center_cols)

    fname = f"davomat_oylik_{month_prefix}.xlsx"
    return Response(
        xlsx_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"; filename*=UTF-8\'\'{quote(fname)}',
            "Cache-Control": "no-store",
        },
    )


@app.route("/api/attendance/bulk", methods=["POST"])
def api_attendance_bulk():
    guard = require_login(["ustoz", "superadmin"], is_api=True)
    if guard:
        return guard
    ensure_day_rollover()
    data = request.get_json(silent=True) or {}
    work_date = (data.get("work_date") or today_str()).strip()
    try:
        datetime.strptime(work_date, "%Y-%m-%d")
    except ValueError:
        work_date = today_str()

    items = data.get("items")
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "error": "Saqlash uchun ma'lumot topilmadi"}), 400

    db = get_db()
    for item in items:
        sid = item.get("student_id")
        status = (item.get("status") or "").strip()
        if not sid or status not in ("+", "-"):
            st_row = db.execute("SELECT full_name FROM students WHERE id = ?", (sid,)).fetchone()
            st_name = st_row["full_name"] if st_row else "o'quvchi"
            return jsonify({
                "ok": False,
                "error": f"Avval shu insonni davomatini oling, so'ng saqlaysiz: {st_name}"
            }), 400

    now_str = now_uz().strftime("%Y-%m-%d %H:%M:%S")
    for item in items:
        sid = item.get("student_id")
        status = (item.get("status") or "").strip()
        reason_type = (item.get("reason_type") or "").strip() if status == "-" else ""
        reason_note = (item.get("reason_note") or "").strip() if status == "-" else ""
        db.execute("""
            INSERT INTO attendance (student_id, work_date, status, reason_type, reason_note, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, work_date) DO UPDATE SET
                status = excluded.status,
                reason_type = excluded.reason_type,
                reason_note = excluded.reason_note,
                updated_at = excluded.updated_at
        """, (sid, work_date, status, reason_type, reason_note, now_str))
    db.commit()
    return jsonify({"ok": True, "saved_count": len(items)})


@app.route("/api/attendance", methods=["POST"])
def api_attendance():
    guard = require_login(["ustoz", "superadmin"], is_api=True)
    if guard:
        return guard
    ensure_day_rollover()
    data = request.get_json(silent=True) or request.form
    student_id = data.get("student_id")
    work_date = (data.get("work_date") or today_str()).strip()
    try:
        datetime.strptime(work_date, "%Y-%m-%d")
    except ValueError:
        work_date = today_str()

    status = (data.get("status") or "").strip()
    reason_type = (data.get("reason_type") or "").strip() if status == "-" else ""
    reason_note = (data.get("reason_note") or "").strip() if status == "-" else ""

    if not student_id or status not in ("+", "-"):
        return jsonify({"ok": False, "error": "Noto'g'ri ma'lumot"}), 400

    db = get_db()
    now_str = now_uz().strftime("%Y-%m-%d %H:%M:%S")
    db.execute("""
        INSERT INTO attendance (student_id, work_date, status, reason_type, reason_note, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(student_id, work_date) DO UPDATE SET
            status = excluded.status,
            reason_type = excluded.reason_type,
            reason_note = excluded.reason_note,
            updated_at = excluded.updated_at
    """, (student_id, work_date, status, reason_type, reason_note, now_str))
    db.commit()
    return jsonify({"ok": True})


@app.route("/phones")
def phones_page():
    guard = require_login(["ustoz", "superadmin"])
    if guard:
        return guard
    return render_page("phones")


@app.route("/phones/set-status", methods=["POST"])
def set_phone_status():
    guard = require_login(["ustoz", "superadmin"])
    if guard:
        return guard
    ensure_day_rollover()
    student_id = request.form.get("student_id")
    work_date = request.form.get("work_date") or today_str()
    phone_status = normalize_phone_status(request.form.get("phone_status"))
    if student_id and phone_status in ("Berildi", "Biz bermadik", "O'quvchi oldi", "O'quvchi bermadi"):
        db = get_db()
        now_dt = now_uz()
        now_hm = now_dt.strftime("%H:%M")
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        phone_given = 1 if phone_status == "Berildi" else 0
        db.execute("""
            INSERT INTO attendance (student_id, work_date, phone_status, phone_given, phone_time, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, work_date) DO UPDATE SET
                phone_status = excluded.phone_status,
                phone_given = excluded.phone_given,
                phone_time = excluded.phone_time,
                updated_at = excluded.updated_at
        """, (student_id, work_date, phone_status, phone_given, now_hm, now_str))
        db.commit()
        flash(f"Holat '{phone_status}' qilib belgilandi.", "success")
    return redirect(url_for("phones_page", date=work_date))


@app.route("/phones/set-all-not-given", methods=["POST"])
def set_all_phones_not_given():
    guard = require_login(["ustoz", "superadmin"])
    if guard:
        return guard
    ensure_day_rollover()
    work_date = request.form.get("work_date") or today_str()
    db = get_db()
    now_dt = now_uz()
    now_hm = now_dt.strftime("%H:%M")
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    students = db.execute("SELECT id FROM students").fetchall()
    for st in students:
        db.execute("""
            INSERT INTO attendance (student_id, work_date, phone_status, phone_given, phone_time, updated_at)
            VALUES (?, ?, 'Biz bermadik', 0, ?, ?)
            ON CONFLICT(student_id, work_date) DO UPDATE SET
                phone_status = 'Biz bermadik',
                phone_given = 0,
                phone_time = excluded.phone_time,
                updated_at = excluded.updated_at
        """, (st["id"], work_date, now_hm, now_str))
    db.commit()
    flash("Barcha o'quvchilarga 'Biz bermadik' belgilandi.", "success")
    return redirect(url_for("phones_page", date=work_date))


@app.route("/phones/save-photo", methods=["POST"])
def save_phone_photo():
    guard = require_login(["ustoz", "superadmin"])
    if guard:
        return guard
    ensure_day_rollover()
    student_id = request.form.get("student_id")
    work_date = request.form.get("work_date") or today_str()
    phone_status = normalize_phone_status(request.form.get("phone_status")) or "Berildi"
    photo_b64 = request.form.get("photo_base64") or ""

    rel_photo = ""
    if "," in photo_b64:
        _, b64_data = photo_b64.split(",", 1)
        try:
            raw_bytes = base64.b64decode(b64_data)
            fname = f"phone_{student_id}_{int(time.time())}.jpg"
            fpath = os.path.join(UPLOAD_DIR, fname)
            with open(fpath, "wb") as f:
                f.write(raw_bytes)
            rel_photo = f"uploads/{fname}"
        except Exception:
            rel_photo = ""

    if student_id:
        db = get_db()
        now_dt = now_uz()
        now_hm = now_dt.strftime("%H:%M")
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        phone_given = 1 if phone_status == "Berildi" else 0
        db.execute("""
            INSERT INTO attendance (student_id, work_date, phone_status, phone_given, phone_photo, phone_time, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, work_date) DO UPDATE SET
                phone_status = excluded.phone_status,
                phone_given = excluded.phone_given,
                phone_photo = CASE WHEN excluded.phone_photo != '' THEN excluded.phone_photo ELSE attendance.phone_photo END,
                phone_time = excluded.phone_time,
                updated_at = excluded.updated_at
        """, (student_id, work_date, phone_status, phone_given, rel_photo, now_hm, now_str))
        db.commit()
        flash("Rasm va holat saqlandi.", "success")
    return redirect(url_for("phones_page", date=work_date))


@app.route("/students")
def students_page():
    guard = require_login(["ustoz", "superadmin"])
    if guard:
        return guard
    return render_page("students")


@app.route("/students/add", methods=["POST"])
def add_student():
    guard = require_login(["ustoz", "superadmin"])
    if guard:
        return guard
    ensure_day_rollover()
    work_date = request.form.get("work_date") or today_str()
    full_name = (request.form.get("full_name") or "").strip()
    class_name = (request.form.get("class_name") or "").strip()
    room_id = request.form.get("room_id")

    if full_name and class_name and room_id:
        db = get_db()
        room = db.execute("SELECT id, capacity FROM rooms WHERE id = ?", (room_id,)).fetchone()
        occ = db.execute("SELECT COUNT(*) AS c FROM students WHERE room_id = ?", (room_id,)).fetchone()["c"]
        if room and occ < room["capacity"]:
            db.execute(
                "INSERT INTO students (full_name, class_name, room_id) VALUES (?, ?, ?)",
                (full_name, class_name, room_id)
            )
            db.commit()
            rewrite_talabalar_txt(db)
            flash("O'quvchi qo'shildi va talabalar.txt ga saqlandi.", "success")
        else:
            flash("Tanlangan xona to'lgan.", "error")
    return redirect(url_for("students_page", date=work_date))


@app.route("/students/delete/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    guard = require_login(["ustoz", "superadmin"])
    if guard:
        return guard
    work_date = request.form.get("work_date") or today_str()
    db = get_db()
    db.execute("DELETE FROM students WHERE id = ?", (student_id,))
    db.commit()
    rewrite_talabalar_txt(db)
    flash("O'quvchi o'chirildi va talabalar.txt yangilandi.", "success")
    return redirect(url_for("students_page", date=work_date))


@app.route("/rooms")
def rooms_page():
    guard = require_login(["ustoz", "superadmin"])
    if guard:
        return guard
    return render_page("rooms")


@app.route("/chef")
def chef_page():
    guard = require_login(["oshpaz", "ustoz", "superadmin"])
    if guard:
        return guard
    return render_page("chef")


@app.route("/superadmin")
def superadmin_page():
    guard = require_login(["superadmin"])
    if guard:
        return guard
    return render_page("superadmin")


@app.route("/superadmin/supervisor", methods=["POST"])
def superadmin_update_supervisor():
    guard = require_login(["superadmin"])
    if guard:
        return guard
    slot_number = request.form.get("slot_number")
    teacher_name = (request.form.get("teacher_name") or "").strip()
    shift_start = (request.form.get("shift_start") or "").strip()
    shift_end = (request.form.get("shift_end") or "").strip()
    work_date = request.form.get("work_date") or today_str()
    if slot_number and teacher_name and shift_start and shift_end:
        db = get_db()
        db.execute("""
            UPDATE supervisors
            SET teacher_name = ?, shift_start = ?, shift_end = ?
            WHERE slot_number = ?
        """, (teacher_name, shift_start, shift_end, slot_number))
        db.commit()
        flash("Navbatchi ma'lumotlari yangilandi.", "success")
    return redirect(url_for("superadmin_page", date=work_date))


@app.route("/superadmin/ontime", methods=["POST"])
def superadmin_toggle_ontime():
    guard = require_login(["superadmin"])
    if guard:
        return guard
    slot_number = request.form.get("slot_number")
    on_time = 1 if request.form.get("on_time") == "1" else 0
    work_date = request.form.get("work_date") or today_str()
    arrived_at = now_uz().strftime("%H:%M") if on_time == 1 else ""
    if slot_number:
        db = get_db()
        db.execute(
            "UPDATE supervisors SET on_time = ?, arrived_at = ? WHERE slot_number = ?",
            (on_time, arrived_at, slot_number)
        )
        db.commit()
    return redirect(url_for("superadmin_page", date=work_date))


@app.route("/superadmin/auto-theme", methods=["POST"])
def superadmin_toggle_auto_theme():
    guard = require_login(["superadmin"])
    if guard:
        return guard
    val = "1" if request.form.get("auto_theme") == "1" else "0"
    work_date = request.form.get("work_date") or today_str()
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('auto_theme', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (val,)
    )
    db.commit()
    return redirect(url_for("superadmin_page", date=work_date))


@app.route("/settings/theme", methods=["POST"])
def set_theme():
    guard = require_login(["ustoz", "oshpaz", "superadmin"])
    if guard:
        return guard
    theme = request.form.get("theme")
    next_url = request.form.get("next_url") or role_home_url(current_role())
    if theme in ("day", "night"):
        session["manual_theme"] = theme
    return redirect(next_url)


@app.route("/settings/device", methods=["POST"])
def set_device():
    guard = require_login(["ustoz", "oshpaz", "superadmin"])
    if guard:
        return guard
    dev = request.form.get("device_mode")
    next_url = request.form.get("next_url") or role_home_url(current_role())
    if dev in ("phone", "desktop"):
        session["device_mode"] = dev
    return redirect(next_url)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)