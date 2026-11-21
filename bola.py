import os
import time
import html
import sqlite3
import base64
from datetime import datetime, date
from flask import (
    Flask, request, redirect, url_for, session, flash,
    get_flashed_messages, g, jsonify
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "yotoqxona-super-admin-2026-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("YOTOQXONA_DB_PATH") or os.path.join(BASE_DIR, "yotoqxona.db")
UPLOAD_DIR = os.environ.get("YOTOQXONA_UPLOAD_DIR") or os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

PINS = {
    "ustoz": os.environ.get("PIN_USTOZ", "1111"),
    "oshpaz": os.environ.get("PIN_OSHPAZ", "2222"),
    "superadmin": os.environ.get("PIN_SUPERADMIN", "9999"),
}

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
failed_logins = {}

LOGO_SVG_DATA_URI = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
    "%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E"
    "%3Cstop offset='0%25' stop-color='%232563eb'/%3E"
    "%3Cstop offset='100%25' stop-color='%230ea5e9'/%3E%3C/linearGradient%3E%3C/defs%3E"
    "%3Crect width='64' height='64' rx='18' fill='url(%23g)'/%3E"
    "%3Cpath d='M16 33L32 19l16 14v15H38V37H26v11H16V33z' fill='%23ffffff'/%3E"
    "%3C/svg%3E"
)

LOGO_BADGE_HTML = (
    f'<span class="brand-logo" aria-hidden="true">'
    f'<img src="{LOGO_SVG_DATA_URI}" alt="Logo" />'
    f'</span>'
)


def esc(val):
    return html.escape(str(val or ""), quote=True)


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
    return date.today().strftime("%Y-%m-%d")


def format_uz_date(iso_date):
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        dt = datetime.now()
    months = [
        "", "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
        "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr"
    ]
    weekdays = [
        "Dushanba", "Seshanba", "Chorshanba", "Payshanba",
        "Juma", "Shanba", "Yakshanba"
    ]
    return f"{dt.day}-{months[dt.month]} {dt.year}-yil, {weekdays[dt.weekday()]}"


def auto_theme_by_hour():
    hour = datetime.now().hour
    return "day" if 6 <= hour < 18 else "night"


def detect_device_mode():
    if session.get("device_mode") in ("phone", "desktop"):
        return session["device_mode"]
    ua = (request.headers.get("User-Agent") or "").lower()
    if any(k in ua for k in ("iphone", "android", "mobile", "ipod", "opera mini", "windows phone")):
        return "phone"
    return "desktop"


def init_db():
    db = sqlite3.connect(DB_PATH)
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

    cur.execute("SELECT COUNT(*) FROM students")
    if cur.fetchone()[0] == 0:
        r1_6 = cur.execute("SELECT id FROM rooms WHERE code = '1c' AND sub_label = '6 kishilik xona'").fetchone()[0]
        r1_4 = cur.execute("SELECT id FROM rooms WHERE code = '1c' AND sub_label = '4 kishilik xona'").fetchone()[0]
        r2_6 = cur.execute("SELECT id FROM rooms WHERE code = '2c' AND sub_label = '6 kishilik xona'").fetchone()[0]
        r3_4 = cur.execute("SELECT id FROM rooms WHERE code = '3c' AND sub_label = '4 kishilik xona'").fetchone()[0]

        sample_students = [
            ("Ali Valiyev", "7-A sinf", r1_6),
            ("Bekzod Karimov", "7-A sinf", r1_6),
            ("Jasur Tursunov", "8-B sinf", r1_6),
            ("Sardor Alimov", "8-B sinf", r1_4),
            ("Azizbek Sobirov", "9-A sinf", r1_4),
            ("Dostonbek Qodirov", "9-A sinf", r2_6),
            ("Sherzod Xolmatov", "10-A sinf", r2_6),
            ("Javohir Abdullayev", "10-B sinf", r3_4),
        ]
        cur.executemany(
            "INSERT INTO students (full_name, class_name, room_id) VALUES (?, ?, ?)",
            sample_students
        )

    db.commit()
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

    students = cur.execute("SELECT id FROM students").fetchall()
    for st in students:
        cur.execute("""
            INSERT OR IGNORE INTO attendance (student_id, work_date, status, phone_status, phone_given)
            VALUES (?, ?, '', '', 0)
        """, (st["id"], cur_date))
    db.commit()


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


def render_page(page_name):
    ensure_day_rollover()
    db = get_db()
    role = current_role()
    theme = active_theme()
    device_mode = detect_device_mode()
    selected_date = request.args.get("date") or today_str()
    try:
        datetime.strptime(selected_date, "%Y-%m-%d")
    except ValueError:
        selected_date = today_str()

    class_filter = request.args.get("class_name", "ALL")
    room_code_filter = request.args.get("room_code", "ALL")

    now_hm = datetime.now().strftime("%H:%M")
    sup_rows = db.execute("SELECT * FROM supervisors ORDER BY slot_number").fetchall()
    supervisors = []
    active_duty_list = []
    for s in sup_rows:
        d = dict(s)
        on_duty = is_time_in_shift(now_hm, d["shift_start"], d["shift_end"])
        d["on_duty"] = on_duty
        supervisors.append(d)
        if on_duty:
            active_duty_list.append(f"{esc(d['teacher_name'])} ({esc(d['shift_start'])}-{esc(d['shift_end'])})")
    duty_banner_text = ", ".join(active_duty_list) if active_duty_list else "Hozir smena vaqtidan tashqari"

    all_students_raw = db.execute("SELECT id FROM students").fetchall()
    for s in all_students_raw:
        db.execute("""
            INSERT OR IGNORE INTO attendance (student_id, work_date, status, phone_status, phone_given)
            VALUES (?, ?, '', '', 0)
        """, (s["id"], selected_date))
    db.commit()

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
    """, (selected_date,)).fetchall()

    students = []
    for r in rows:
        d = dict(r)
        if not d["phone_status"] and d["phone_given"] == 1:
            d["phone_status"] = "Berildi"
        students.append(d)

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
    phone_taken_count = sum(1 for st in students if st["phone_status"] == "Olingan")
    phone_not_given_count = sum(1 for st in students if st["phone_status"] == "Berilmadi")

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
        role_badge_html = f'''
          <span class="role-pill role-{esc(role)}">{esc(r_label)}</span>
          <form method="POST" action="{url_for('logout')}" class="inline-form">
            <button type="submit" class="btn btn-outline btn-sm">Chiqish</button>
          </form>
        '''

    theme_btn_label = "Kechki rejim" if theme == "day" else "Kunduzgi rejim"
    theme_next_val = "night" if theme == "day" else "day"

    device_btn_label = "Kompyuter rejimi" if device_mode == "phone" else "Telefon rejimi"
    device_next_val = "desktop" if device_mode == "phone" else "phone"

    if page_name == "login":
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
            <form method="POST" action="{url_for('login')}">
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
          <a href="{url_for('chef_page')}" class="btn btn-primary">Yangilash</a>
        </section>
        """
    else:
        super_tab = (
            f'<a href="{url_for("superadmin_page", date=selected_date)}" class="nav-tab {"active" if page_name == "superadmin" else ""}">5. Super Admin</a>'
            if role == "superadmin" else ""
        )
        endpoint_name = request.endpoint if request.endpoint in (
            "attendance_page", "phones_page", "students_page", "rooms_page", "superadmin_page"
        ) else "attendance_page"
        today_btn = (
            f'<a href="{url_for(endpoint_name, date=today_str())}" class="btn btn-primary btn-sm">Bugun</a>'
            if selected_date != today_str() else ""
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
            rows_html = ""
            for idx, st in enumerate(filtered_students, start=1):
                active_cls = "active-row" if idx == 1 else ""
                p_cls = "is-plus" if st["status"] == "+" else ""
                m_cls = "is-minus" if st["status"] == "-" else ""
                tag_txt = "Bor" if st["status"] == "+" else ("Yo'q" if st["status"] == "-" else "—")
                r_disp = ""
                if st["status"] == "-":
                    r_disp = f'{esc(st["reason_type"])}: {esc(st["reason_note"])}' if st["reason_note"] else esc(st["reason_type"])
                note_hidden = "" if st["reason_type"] == "Boshqa sabab" else "hidden"
                rows_html += f"""
                <tr class="excel-row {active_cls}" tabindex="0" data-student-id="{st['id']}" data-status="{esc(st['status'])}" onclick="focusRow(this)">
                  <td>{idx}</td>
                  <td><strong>{esc(st['full_name'])}</strong></td>
                  <td>{esc(st['class_name'])}</td>
                  <td>{esc(st['room_code'])} <small>({st['room_capacity']} k.)</small></td>
                  <td>
                    <button type="button" class="cell-btn plus-btn {p_cls}" onclick="event.stopPropagation(); setRowPlus(this.closest('tr'))">+</button>
                    <button type="button" class="cell-btn minus-btn {m_cls}" onclick="event.stopPropagation(); openRowMinus(this.closest('tr'))">-</button>
                    <span class="status-tag" id="tag-{st['id']}">{tag_txt}</span>
                  </td>
                  <td>
                    <div id="reason-display-{st['id']}" class="reason-display">{r_disp}</div>
                    <div class="inline-reason-editor hidden" id="editor-{st['id']}" onclick="event.stopPropagation()">
                      <select class="input input-sm" id="rtype-{st['id']}" onchange="checkOtherInput({st['id']})">
                        <option value="Sababli" {"selected" if st["reason_type"] == "Sababli" else ""}>Sababli</option>
                        <option value="O'zboshimchalik bilan" {"selected" if st["reason_type"] == "O'zboshimchalik bilan" else ""}>O'zboshimchalik bilan</option>
                        <option value="Boshqa sabab" {"selected" if st["reason_type"] == "Boshqa sabab" else ""}>Boshqa sabab</option>
                      </select>
                      <input type="text" class="input input-sm {note_hidden}" id="rnote-{st['id']}" value="{esc(st['reason_note'])}" placeholder="Izoh..." />
                      <button type="button" class="btn btn-danger btn-sm" onclick="saveRowMinus({st['id']}, this.closest('tr'))">Saqlash &rarr;</button>
                    </div>
                  </td>
                </tr>
                """
            sub_content = f"""
            <section class="glass-card section-box">
              <div class="section-header">
                <div>
                  <h2>Excel Davomat ({esc(format_uz_date(selected_date))})</h2>
                  <p class="muted">Qatorni tanlang yoki &uarr;/&darr; bilan yuring: <strong>+</strong> bossangiz "Bor" bo'lib keyingi qatorga o'tadi, <strong>-</strong> bossangiz sabab ochiladi.</p>
                </div>
                <form method="GET" action="{url_for('attendance_page')}" class="filter-bar">
                  <input type="hidden" name="date" value="{esc(selected_date)}" />
                  <select name="class_name" class="input input-sm" onchange="this.form.submit()">
                    <option value="ALL" {"selected" if class_filter == "ALL" else ""}>Barcha sinflar</option>
                    {class_opts}
                  </select>
                  <select name="room_code" class="input input-sm" onchange="this.form.submit()">
                    <option value="ALL" {"selected" if room_code_filter == "ALL" else ""}>1c-12c barchasi</option>
                    {room_opts}
                  </select>
                </form>
              </div>
              <div class="table-wrap">
                <table class="excel-table" id="excel-attendance-table">
                  <thead>
                    <tr><th>#</th><th>O'quvchi</th><th>Sinf</th><th>Xona</th><th>+ / -</th><th>Sabab</th></tr>
                  </thead>
                  <tbody>{rows_html}</tbody>
                </table>
              </div>
            </section>
            """
        elif page_name == "phones":
            p_rows = ""
            for st in students:
                ps = st["phone_status"]
                if ps == "Berildi":
                    p_badge = f'<span class="badge badge-phone">Berildi ({esc(st["phone_time"])})</span>'
                elif ps == "Olingan":
                    p_badge = f'<span class="badge badge-taken">Olingan ({esc(st["phone_time"])})</span>'
                elif ps == "Berilmadi":
                    p_badge = f'<span class="badge badge-notgiven">Berilmadi ({esc(st["phone_time"])})</span>'
                else:
                    p_badge = '<span class="badge badge-none">Belgilanmagan</span>'

                p_img = f'<a href="{url_for("static", filename=st["phone_photo"])}" target="_blank" class="btn btn-soft btn-sm">Ko\'rish</a>' if st["phone_photo"] else "—"
                safe_name = esc(st["full_name"]).replace("'", "")

                p_rows += f"""
                <tr>
                  <td><strong>{esc(st['full_name'])}</strong></td>
                  <td>{esc(st['class_name'])} &middot; {esc(st['room_code'])}</td>
                  <td>{p_badge}</td>
                  <td>
                    <div class="phone-btn-group">
                      <button type="button" class="btn btn-primary btn-sm" onclick="openLiveCamera({st['id']}, '{safe_name}', 'Berildi')">Berildi (Kamera)</button>
                      <button type="button" class="btn btn-soft btn-sm" onclick="openLiveCamera({st['id']}, '{safe_name}', 'Olingan')">Olingan (Kamera)</button>
                      <form method="POST" action="{url_for('set_phone_status')}" class="inline-form">
                        <input type="hidden" name="student_id" value="{st['id']}" />
                        <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                        <input type="hidden" name="phone_status" value="Berilmadi" />
                        <button type="submit" class="btn btn-outline btn-sm">Berilmadi</button>
                      </form>
                    </div>
                  </td>
                  <td>{p_img}</td>
                </tr>
                """
            sub_content = f"""
            <section class="glass-card section-box">
              <div class="section-header">
                <div>
                  <h2>Telefon Nazorati — Berildi / Olingan / Berilmadi</h2>
                  <p class="muted">Berildi yoki Olingan belgilanganda jonli kamera ochiladi. Berilmadi esa tepadan hammaga bir tugmada qo'yiladi.</p>
                </div>
                <form method="POST" action="{url_for('set_all_phones_not_given')}" onsubmit="return confirm('Ro\'yxatdagi barcha o\'quvchilarga \'Berilmadi\' belgilansinmi?');">
                  <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                  <button type="submit" class="btn btn-danger">Berilmadi (Hammaga)</button>
                </form>
              </div>

              <div class="phone-counters mb-16">
                <span class="chip">Berildi: <strong>{phone_given_count}</strong></span>
                <span class="chip">Olingan: <strong>{phone_taken_count}</strong></span>
                <span class="chip">Berilmadi: <strong>{phone_not_given_count}</strong></span>
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
                  <button type="button" class="btn btn-primary full-width" onclick="capturePhoto()">Rasmga olish va Tasdiqlash</button>
                </div>
                <form id="photo-submit-form" method="POST" action="{url_for('save_phone_photo')}" class="hidden">
                  <input type="hidden" name="student_id" id="photo-student-id" />
                  <input type="hidden" name="phone_status" id="photo-phone-status" value="Berildi" />
                  <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                  <input type="hidden" name="photo_base64" id="photo-base64" />
                </form>
              </div>

              <div class="table-wrap">
                <table class="excel-table">
                  <thead><tr><th>O'quvchi</th><th>Sinf / Xona</th><th>Holat</th><th>Amallar</th><th>Isbot rasm</th></tr></thead>
                  <tbody>{p_rows}</tbody>
                </table>
              </div>
            </section>
            """
        elif page_name == "students":
            room_select_opts = ""
            for rg in rooms_grouped:
                dis = "disabled" if rg["occupied_count"] >= rg["capacity"] else ""
                room_select_opts += f'<option value="{rg["id"]}" {dis}>{esc(rg["code"])} — {esc(rg["sub_label"])} ({rg["occupied_count"]}/{rg["capacity"]})</option>'
            st_rows = ""
            for st in students:
                st_rows += f"""
                <tr>
                  <td>{esc(st['full_name'])}</td>
                  <td>{esc(st['class_name'])}</td>
                  <td>{esc(st['room_code'])} ({esc(st['room_sub_label'])})</td>
                  <td>
                    <form method="POST" action="{url_for('delete_student', student_id=st['id'])}" onsubmit="return confirm('O\'chirilsinmi?');">
                      <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                      <button type="submit" class="btn btn-danger btn-sm">O'chirish</button>
                    </form>
                  </td>
                </tr>
                """
            sub_content = f"""
            <section class="glass-card section-box">
              <h2>O'quvchi Qo'shish va Ro'yxat</h2>
              <form method="POST" action="{url_for('add_student')}" class="form-grid mb-16">
                <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                <div>
                  <label class="field-label">Ism-familiya</label>
                  <input type="text" name="full_name" class="input" required />
                </div>
                <div>
                  <label class="field-label">Sinf</label>
                  <input type="text" name="class_name" class="input" placeholder="Masalan: 7-A sinf" required />
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
                <table class="excel-table">
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
                    lis = "".join(
                        f'<li><span>{esc(o["full_name"])} <small>({esc(o["class_name"])})</small></span><span class="mini-badge {"plus" if o["status"]=="+" else ("minus" if o["status"]=="-" else "wait")}">{esc(o["status"]) or "?"}</span></li>'
                        for o in rg["occupants"]
                    )
                    occ_html = f'<ul class="occupant-list">{lis}</ul>'
                else:
                    occ_html = '<p class="empty-room">Bo\'sh xona</p>'
                r_cards += f"""
                <div class="room-card">
                  <div class="room-card-top">
                    <span class="room-code-badge">{esc(rg['code'])}</span>
                    <span class="room-cap">{esc(rg['sub_label'])} &middot; <strong>{rg['occupied_count']}/{rg['capacity']}</strong></span>
                  </div>
                  <div class="room-present-note">Bor (+): <strong>{rg['present_in_room']}</strong></div>
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
            sup_cards_html = ""
            for sp in supervisors:
                duty_pill = '<span class="badge badge-phone">Hozir navbatchi</span>' if sp["on_duty"] else '<span class="badge badge-none">Navbatda emas</span>'
                ontime_pill = f'<span class="badge badge-plus">Ishiga o\'z vaqtida keldi ({esc(sp["arrived_at"])})</span>' if sp["on_time"] == 1 else '<span class="badge badge-none">Hali belgilanmagan</span>'
                sub_val = "0" if sp["on_time"] == 1 else "1"
                sub_lbl = "Belginini olish" if sp["on_time"] == 1 else "Ishiga o'z vaqtida keldi"
                sup_cards_html += f"""
                <div class="room-card">
                  <div class="room-card-top">
                    <span class="room-code-badge">{sp['slot_number']}-Navbat</span>
                    {duty_pill}
                  </div>
                  <div class="mt-12">{ontime_pill}</div>
                  <form method="POST" action="{url_for('superadmin_update_supervisor')}" class="mt-12">
                    <input type="hidden" name="slot_number" value="{sp['slot_number']}" />
                    <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                    <label class="field-label">Ustoz ismi</label>
                    <input type="text" name="teacher_name" class="input input-sm" value="{esc(sp['teacher_name'])}" required />
                    <div class="form-grid mt-12">
                      <div>
                        <label class="field-label">Smena boshlanishi</label>
                        <input type="text" name="shift_start" class="input input-sm" value="{esc(sp['shift_start'])}" required />
                      </div>
                      <div>
                        <label class="field-label">Smena tugashi</label>
                        <input type="text" name="shift_end" class="input input-sm" value="{esc(sp['shift_end'])}" required />
                      </div>
                    </div>
                    <button type="submit" class="btn btn-primary btn-sm full-width mt-12">Saqlash</button>
                  </form>
                  <form method="POST" action="{url_for('superadmin_toggle_ontime')}" class="mt-12">
                    <input type="hidden" name="slot_number" value="{sp['slot_number']}" />
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
              <form method="POST" action="{url_for('superadmin_toggle_auto_theme')}" class="mb-16">
                <input type="hidden" name="work_date" value="{esc(selected_date)}" />
                <label class="checkbox-inline">
                  <input type="checkbox" name="auto_theme" value="1" {chk} onchange="this.form.submit()" />
                  <span>Soatga qarab avtomatik Kunduzgi / Kechki rejim</span>
                </label>
              </form>
              <div class="rooms-grid">{sup_cards_html}</div>
            </section>
            """

        body_html = f"""
        <header class="topbar glass-card">
          <div class="brand-area">
            {LOGO_BADGE_HTML}
            <div>
              <div class="brand-title">Yotoqxona Nazorati</div>
              <div class="brand-sub">{esc(format_uz_date(selected_date))} &middot; Navbatchi: <strong>{duty_banner_text}</strong></div>
            </div>
          </div>
          <div class="top-actions">
            <form method="GET" action="{url_for(endpoint_name)}" class="inline-form date-form">
              <input type="date" name="date" value="{esc(selected_date)}" class="input input-sm" onchange="this.form.submit()" />
            </form>
            {today_btn}
            <form method="POST" action="{url_for('set_theme')}" class="inline-form">
              <input type="hidden" name="theme" value="{theme_next_val}" />
              <input type="hidden" name="next_url" value="{esc(request.full_path)}" />
              <button type="submit" class="btn btn-outline btn-sm">{theme_btn_label}</button>
            </form>
            <form method="POST" action="{url_for('set_device')}" class="inline-form">
              <input type="hidden" name="device_mode" value="{device_next_val}" />
              <input type="hidden" name="next_url" value="{esc(request.full_path)}" />
              <button type="submit" class="btn btn-outline btn-sm">{device_btn_label}</button>
            </form>
            {role_badge_html}
          </div>
        </header>

        <nav class="nav-tabs glass-card">
          <a href="{url_for('attendance_page', date=selected_date)}" class="nav-tab {'active' if page_name == 'attendance' else ''}">1. Davomat</a>
          <a href="{url_for('phones_page', date=selected_date)}" class="nav-tab {'active' if page_name == 'phones' else ''}">2. Telefon</a>
          <a href="{url_for('students_page', date=selected_date)}" class="nav-tab {'active' if page_name == 'students' else ''}">3. O'quvchilar</a>
          <a href="{url_for('rooms_page', date=selected_date)}" class="nav-tab {'active' if page_name == 'rooms' else ''}">4. Xonalar</a>
          <a href="{url_for('chef_page', date=selected_date)}" class="nav-tab {'active' if page_name == 'chef' else ''}">Oshpaz</a>
          {super_tab}
        </nav>

        <section class="stats-strip">
          <div class="stat-tile glass-card"><span>Jami</span><strong>{total_count}</strong></div>
          <div class="stat-tile glass-card"><span>Bor (+)</span><strong>{present_count}</strong></div>
          <div class="stat-tile glass-card"><span>Yo'q (-)</span><strong>{absent_count}</strong></div>
          <div class="stat-tile glass-card"><span>Belgilanmagan</span><strong>{unmarked_count}</strong></div>
        </section>

        {sub_content}
        """

    return f"""<!DOCTYPE html>
<html lang="uz" data-theme="{esc(theme)}" data-device="{esc(device_mode)}">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Yotoqxona Nazorati</title>
  <link rel="icon" type="image/svg+xml" href="{LOGO_SVG_DATA_URI}" />
  <style>
    :root {{
      --bg: #eef2f7;
      --card: rgba(255, 255, 255, 0.88);
      --text: #0f172a;
      --muted: #64748b;
      --border: rgba(15, 23, 42, 0.12);
      --primary: #2563eb;
      --danger: #dc2626;
      --ok: #16a34a;
      --warn: #d97706;
    }}
    html[data-theme="night"] {{
      --bg: #0b1120;
      --card: rgba(15, 23, 42, 0.88);
      --text: #f8fafc;
      --muted: #94a3b8;
      --border: rgba(148, 163, 184, 0.2);
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
    .brand-logo img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
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
    .filter-bar {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .table-wrap {{ overflow-x: auto; }}
    .excel-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    .excel-table th, .excel-table td {{
      padding: 10px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: middle;
    }}
    .excel-row.active-row {{ outline: 2px solid var(--primary); background: rgba(37, 99, 235, 0.06); }}
    .cell-btn {{
      width: 30px; height: 30px; border-radius: 8px; border: 1px solid var(--border);
      background: transparent; color: var(--text); font-weight: 700; cursor: pointer;
    }}
    .cell-btn.is-plus {{ background: var(--ok); color: #fff; border-color: var(--ok); }}
    .cell-btn.is-minus {{ background: var(--danger); color: #fff; border-color: var(--danger); }}
    .status-tag {{ margin-left: 6px; font-weight: 600; font-size: 12px; }}
    .inline-reason-editor {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }}
    .hidden {{ display: none !important; }}
    .input {{
      padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border);
      background: transparent; color: var(--text); font-size: 14px;
    }}
    .input-sm {{ padding: 6px 8px; font-size: 13px; }}
    .btn {{
      padding: 8px 14px; border-radius: 10px; border: 1px solid transparent;
      font-weight: 600; font-size: 14px; cursor: pointer; text-decoration: none; display: inline-flex;
      align-items: center; justify-content: center;
    }}
    .btn-sm {{ padding: 5px 10px; font-size: 12px; }}
    .btn-primary {{ background: var(--primary); color: #fff; }}
    .btn-danger {{ background: var(--danger); color: #fff; }}
    .btn-soft {{ background: rgba(37, 99, 235, 0.12); color: var(--primary); }}
    .btn-outline {{ border-color: var(--border); background: transparent; color: var(--text); }}
    .full-width {{ width: 100%; }}
    .mt-12 {{ margin-top: 12px; }}
    .mb-16 {{ margin-bottom: 16px; }}
    .chip-row, .phone-counters, .phone-btn-group {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .chip {{ padding: 6px 10px; border-radius: 999px; border: 1px solid var(--border); font-size: 13px; }}
    .badge {{ padding: 4px 8px; border-radius: 8px; font-size: 12px; font-weight: 600; }}
    .badge-phone {{ background: rgba(22, 163, 74, 0.15); color: var(--ok); }}
    .badge-taken {{ background: rgba(37, 99, 235, 0.15); color: var(--primary); }}
    .badge-notgiven {{ background: rgba(217, 119, 6, 0.15); color: var(--warn); }}
    .badge-plus {{ background: rgba(22, 163, 74, 0.15); color: var(--ok); }}
    .badge-none {{ background: rgba(148, 163, 184, 0.18); color: var(--muted); }}
    .role-pill {{ padding: 5px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; background: rgba(37, 99, 235, 0.15); }}
    .alert {{ padding: 10px 14px; border-radius: 10px; margin-bottom: 12px; font-weight: 600; }}
    .alert-error {{ background: rgba(220, 38, 38, 0.15); color: var(--danger); }}
    .alert-success {{ background: rgba(22, 163, 74, 0.15); color: var(--ok); }}
    .login-wrapper {{ display: flex; justify-content: center; padding: 32px 0; }}
    .login-card {{ width: 100%; max-width: 420px; }}
    .login-brand {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }}
    .login-brand h1 {{ margin: 0 0 2px; font-size: 22px; }}
    .field-label {{ display: block; font-size: 13px; font-weight: 600; margin-bottom: 4px; }}
    .role-select-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 12px; }}
    .role-option {{
      border: 1px solid var(--border); border-radius: 10px; padding: 8px;
      display: flex; gap: 6px; align-items: flex-start; cursor: pointer;
    }}
    .role-option span {{ display: flex; flex-direction: column; font-size: 12px; }}
    .chef-section {{ text-align: center; max-width: 680px; margin: 20px auto; }}
    .chef-big-number {{ font-size: 56px; font-weight: 800; color: var(--primary); margin: 8px 0; }}
    .chef-big-number span {{ font-size: 20px; font-weight: 600; }}
    .chef-substats {{ display: flex; justify-content: center; gap: 20px; margin-bottom: 16px; }}
    .form-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }}
    .align-end {{ display: flex; align-items: flex-end; }}
    .rooms-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 12px; }}
    .room-card {{ border: 1px solid var(--border); border-radius: 12px; padding: 12px; }}
    .room-card-top {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
    .room-code-badge {{ font-weight: 800; font-size: 15px; }}
    .occupant-list {{ list-style: none; padding: 0; margin: 8px 0 0; }}
    .occupant-list li {{ display: flex; justify-content: space-between; padding: 4px 0; border-top: 1px dashed var(--border); font-size: 13px; }}
    .mini-badge.plus {{ color: var(--ok); font-weight: 700; }}
    .mini-badge.minus {{ color: var(--danger); font-weight: 700; }}
    .camera-panel {{ border: 1px solid var(--border); border-radius: 12px; padding: 12px; margin-bottom: 16px; }}
    .camera-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
    .camera-viewport video {{ width: 100%; max-height: 320px; border-radius: 10px; background: #000; }}
    .checkbox-inline {{ display: inline-flex; align-items: center; gap: 8px; cursor: pointer; }}
    html[data-device="phone"] .container {{ max-width: 520px; }}
  </style>
</head>
<body>
  <main class="container">
    {flash_html}
    {body_html}
  </main>
  <script>
    const CURRENT_DATE = "{esc(selected_date)}";
    let cameraStream = null;

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

    async function setRowPlus(row) {{
      if (!row) return;
      const sid = row.dataset.studentId;
      const res = await fetch("{url_for('api_attendance')}", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ student_id: sid, work_date: CURRENT_DATE, status: "+" }})
      }});
      if (res.ok) {{
        row.dataset.status = "+";
        row.querySelector(".plus-btn")?.classList.add("is-plus");
        row.querySelector(".minus-btn")?.classList.remove("is-minus");
        const tag = document.getElementById("tag-" + sid);
        if (tag) tag.textContent = "Bor";
        const rdisp = document.getElementById("reason-display-" + sid);
        if (rdisp) rdisp.textContent = "";
        document.getElementById("editor-" + sid)?.classList.add("hidden");
        moveRow(1);
      }}
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

    async function saveRowMinus(sid, row) {{
      const rtype = document.getElementById("rtype-" + sid)?.value || "Sababli";
      const rnote = document.getElementById("rnote-" + sid)?.value || "";
      const res = await fetch("{url_for('api_attendance')}", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{
          student_id: sid,
          work_date: CURRENT_DATE,
          status: "-",
          reason_type: rtype,
          reason_note: rnote
        }})
      }});
      if (res.ok) {{
        if (row) {{
          row.dataset.status = "-";
          row.querySelector(".minus-btn")?.classList.add("is-minus");
          row.querySelector(".plus-btn")?.classList.remove("is-plus");
        }}
        const tag = document.getElementById("tag-" + sid);
        if (tag) tag.textContent = "Yo'q";
        const rdisp = document.getElementById("reason-display-" + sid);
        if (rdisp) rdisp.textContent = rnote ? `${{rtype}}: ${{rnote}}` : rtype;
        document.getElementById("editor-" + sid)?.classList.add("hidden");
        moveRow(1);
      }}
    }}

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

    async function openLiveCamera(studentId, studentName, phoneStatus) {{
      const modal = document.getElementById("camera-modal");
      const errBox = document.getElementById("camera-error");
      const video = document.getElementById("camera-video");
      if (!modal || !video) return;
      document.getElementById("camera-target-name").textContent = `${{studentName}} — ${{phoneStatus}}`;
      document.getElementById("photo-student-id").value = studentId;
      document.getElementById("photo-phone-status").value = phoneStatus;
      errBox?.classList.add("hidden");
      modal.classList.remove("hidden");
      try {{
        cameraStream = await navigator.mediaDevices.getUserMedia({{ video: {{ facingMode: "environment" }}, audio: false }});
        video.srcObject = cameraStream;
      }} catch (err) {{
        if (errBox) {{
          errBox.textContent = "Kamerani ochib bo'lmadi: " + (err.message || err);
          errBox.classList.remove("hidden");
        }}
      }}
    }}

    function closeCamera() {{
      if (cameraStream) {{
        cameraStream.getTracks().forEach(t => t.stop());
        cameraStream = null;
      }}
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


@app.route("/api/attendance", methods=["POST"])
def api_attendance():
    guard = require_login(["ustoz", "superadmin"], is_api=True)
    if guard:
        return guard
    ensure_day_rollover()
    data = request.get_json(silent=True) or request.form
    student_id = data.get("student_id")
    work_date = data.get("work_date") or today_str()
    status = (data.get("status") or "").strip()
    reason_type = (data.get("reason_type") or "").strip() if status == "-" else ""
    reason_note = (data.get("reason_note") or "").strip() if status == "-" else ""

    if not student_id or status not in ("+", "-"):
        return jsonify({"ok": False, "error": "Noto'g'ri ma'lumot"}), 400

    db = get_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    phone_status = (request.form.get("phone_status") or "").strip()
    if student_id and phone_status in ("Berildi", "Olingan", "Berilmadi"):
        db = get_db()
        now_hm = datetime.now().strftime("%H:%M")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    now_hm = datetime.now().strftime("%H:%M")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    students = db.execute("SELECT id FROM students").fetchall()
    for st in students:
        db.execute("""
            INSERT INTO attendance (student_id, work_date, phone_status, phone_given, phone_time, updated_at)
            VALUES (?, ?, 'Berilmadi', 0, ?, ?)
            ON CONFLICT(student_id, work_date) DO UPDATE SET
                phone_status = 'Berilmadi',
                phone_given = 0,
                phone_time = excluded.phone_time,
                updated_at = excluded.updated_at
        """, (st["id"], work_date, now_hm, now_str))
    db.commit()
    flash("Barcha o'quvchilarga 'Berilmadi' belgilandi.", "success")
    return redirect(url_for("phones_page", date=work_date))


@app.route("/phones/save-photo", methods=["POST"])
def save_phone_photo():
    guard = require_login(["ustoz", "superadmin"])
    if guard:
        return guard
    ensure_day_rollover()
    student_id = request.form.get("student_id")
    work_date = request.form.get("work_date") or today_str()
    phone_status = (request.form.get("phone_status") or "Berildi").strip()
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
        now_hm = datetime.now().strftime("%H:%M")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
            flash("O'quvchi qo'shildi.", "success")
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
    flash("O'quvchi o'chirildi.", "success")
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
    arrived_at = datetime.now().strftime("%H:%M") if on_time == 1 else ""
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