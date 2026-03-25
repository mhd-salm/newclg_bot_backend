"""
app.py
──────
Application entry point.

Changes vs previous version:
1. Admin blueprint registered at /admin.
2. DayOrderOverride DB table is checked FIRST before rr.txt for day order queries.
3. TimetableEntry DB table is used when available, falls back to rr.txt chunks.
4. JWT claims now include 'role' (student | admin).
5. ADMIN_SECRET_KEY env var required for admin registration.
"""
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import os
import re
import sys
import logging
from datetime import datetime, date, timedelta

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from dotenv import load_dotenv

import google.generativeai as genai

from config     import config_map
from extensions import db, bcrypt, jwt, limiter
from models     import Student, DayOrderOverride, TimetableEntry, Announcement
from auth       import auth_bp
from admin      import admin_bp


# ══════════════════════════════════════════════════════════════════════════════
#  Application Factory
# ══════════════════════════════════════════════════════════════════════════════

def create_app(env: str = None) -> Flask:
    load_dotenv()

    env = env or os.getenv("FLASK_ENV", "default")
    app = Flask(__name__)
    app.config.from_object(config_map[env])

    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY")
    app.config["GEMINI_KEYS"] = os.getenv("GEMINI_KEYS")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    app.logger.setLevel(logging.INFO)

    db.init_app(app)
    bcrypt.init_app(app)
    jwt.init_app(app)
    limiter.init_app(app)

    CORS(
        app,
        resources={
            r"/chat":        {"origins": "*"},
            r"/auth/*":      {"origins": "*"},
            r"/admin/*":     {"origins": "*"},
        },
        supports_credentials=True,
    )

    app.register_blueprint(auth_bp,  url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    with app.app_context():
        db.create_all()
        app.logger.info("✅ Database tables verified / created.")

    _register_chat_route(app)
    return app


# ══════════════════════════════════════════════════════════════════════════════
#  Gemini Key Failover  (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

_gemini_keys:       list[str] = []
_current_key_index: int       = 0

MODEL_NAME = "gemini-2.5-flash-lite"

def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default

GEMINI_CHUNK_MAX_CHARS  = _int_env("GEMINI_CHUNK_MAX_CHARS",   1400)
GEMINI_RETRIEVAL_CHUNKS = _int_env("GEMINI_RETRIEVAL_CHUNKS",  2)
MAX_OUTPUT_TOKENS       = _int_env("GEMINI_MAX_OUTPUT_TOKENS", 180)


def _load_gemini_keys(app: Flask):
    global _gemini_keys
    raw = app.config.get("GEMINI_KEYS", "")
    _gemini_keys = [k.strip() for k in raw.split(",") if k.strip()]
    app.logger.info(f"🔍 Gemini keys loaded: {len(_gemini_keys)}")
    if not _gemini_keys:
        raise RuntimeError("No Gemini API keys found in GEMINI_KEYS")


def _get_gemini_model():
    global _current_key_index
    attempts   = 0
    last_error = None
    while attempts < len(_gemini_keys):
        api_key    = _gemini_keys[_current_key_index]
        key_number = _current_key_index + 1
        try:
            genai.configure(api_key=api_key)
            return genai.GenerativeModel(
                model_name        = MODEL_NAME,
                generation_config = {
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                    "temperature":       0.2,
                },
            )
        except Exception as e:
            import logging as _log
            _log.warning(f"❌ Gemini Key-{key_number} failed. Switching key.")
            last_error = e
            _current_key_index += 1
            attempts += 1
            if _current_key_index >= len(_gemini_keys):
                _log.info("🔁 All keys exhausted. Resetting to Key-1")
                _current_key_index = 0
                break
    raise RuntimeError(f"All Gemini API keys failed: {last_error}")


# ══════════════════════════════════════════════════════════════════════════════
#  System Prompt
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = (
    "You are a concise factual assistant for The New College, Chennai. "
    '"newcollege" means this college. Be brief.'
)


# ══════════════════════════════════════════════════════════════════════════════
#  Data Files & Chunk Loading  (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

DATA_FILES = {
    "college":    "college.txt",
    "shift1":     "shift1.txt",
    "shift2":     "shift2.txt",
    "timetable":  "rr.txt",
    "developers": "dev.txt",
}

def _load_chunks():
    chunks = []
    for tag, file in DATA_FILES.items():
        if not os.path.exists(file):
            continue
        with open(file, "r", encoding="utf-8") as f:
            text  = f.read()
            parts = re.split(r"\n\s*\n", text)
            for p in parts:
                p = p.strip()
                if len(p) > 40:
                    chunks.append({"tag": tag, "text": p.lower(), "raw": p})
    return chunks

CHUNKS = _load_chunks()

INTENT_KEYWORDS = {
    "developers": [
        "who made", "who created", "developer", "built you",
        "salman", "mustansir", "shahid", "sathya",
    ],
    "timetable": [
        "timetable", "time table", "schedule", "period",
        "day order", "dayorder", "class", "classes", "lecture",
        "what do i have", "what do we have",
    ],
    "shift1": [
        "shift 1", "shift one", "morning",
        "fee", "fees", "fee structure", "how much",
    ],
    "shift2": [
        "shift 2", "shift two", "evening",
        "fee", "fees", "fee structure", "how much",
    ],
    "college": [
        "college", "new college", "newcollege",
        "about", "history", "principal",
        "address", "contact", "department", "course",
    ],
}


def _trim_chunk(raw: str) -> str:
    cap = GEMINI_CHUNK_MAX_CHARS
    if len(raw) <= cap:
        return raw
    return raw[: cap - 3].rstrip() + "..."


def _select_relevant_chunks(user_msg: str, limit: int | None = None) -> list[str]:
    if limit is None:
        limit = GEMINI_RETRIEVAL_CHUNKS
    user_msg_l    = user_msg.lower()
    keywords      = user_msg_l.split()
    scored        = []
    detected_tags = {
        tag for tag, words in INTENT_KEYWORDS.items()
        if any(w in user_msg_l for w in words)
    }
    for c in CHUNKS:
        if detected_tags and c["tag"] not in detected_tags:
            continue
        score = sum(1 for k in keywords if k in c["text"])
        intent_words = INTENT_KEYWORDS.get(c["tag"], [])
        if any(w in user_msg_l for w in intent_words):
            score += 50
        if c["tag"] == "developers" and any(
            k in user_msg_l for k in [
                "who made", "who created", "developer", "built you",
                "salman", "mustansir", "shahid", "sathya",
            ]
        ):
            score += 100
        if score > 0:
            scored.append((score, c["raw"]))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [_trim_chunk(s[1]) for s in scored[:limit]]


# ══════════════════════════════════════════════════════════════════════════════
#  Date Context
# ══════════════════════════════════════════════════════════════════════════════

_DATE_KEYWORDS = [
    "today", "tomorrow", "timetable", "schedule",
    "day order", "dayorder",
    "monday", "tuesday", "wednesday", "thursday", "friday",
]

def _needs_date_context(user_msg: str) -> bool:
    msg = user_msg.lower()
    return any(k in msg for k in _DATE_KEYWORDS)

def _get_current_date_context() -> str:
    now = datetime.now()
    return f"Date: {now.strftime('%Y-%m-%d')} ({now.strftime('%A')})."


# ══════════════════════════════════════════════════════════════════════════════
#  Day Order — DB override takes priority over rr.txt
# ══════════════════════════════════════════════════════════════════════════════

_DAY_ORDER_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI"}

# rr.txt calendar (fallback)
_day_order_calendar: dict[str, object] | None = None
_RR_DATE_LINE_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})\s+(\w+)\s+(.+)$")


def _load_day_order_calendar() -> dict[str, object]:
    global _day_order_calendar
    if _day_order_calendar is not None:
        return _day_order_calendar
    path = DATA_FILES.get("timetable", "rr.txt")
    cal: dict[str, object] = {}
    if not os.path.exists(path):
        _day_order_calendar = cal
        return cal
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            m = _RR_DATE_LINE_RE.match(line)
            if not m:
                continue
            dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
            rest = m.group(5).strip().strip("`").strip()
            iso  = f"{yyyy}-{mm}-{dd}"
            if rest.lower().startswith("holiday"):
                cal[iso] = "holiday"
                continue
            num_match = re.search(r"(\d+)", rest)
            if num_match:
                n = int(num_match.group(1))
                if 1 <= n <= 6:
                    cal[iso] = n
    _day_order_calendar = cal
    return cal


def _get_day_order_for_iso(iso: str) -> tuple[str, int | None]:
    """
    Check DB override first, then rr.txt.
    Returns (kind, value): kind = 'number'|'holiday'|'missing'
    """
    # 1. DB override
    try:
        parsed = date.fromisoformat(iso)
        override = DayOrderOverride.query.filter_by(date=parsed).first()
        if override is not None:
            if override.day_order == 0:
                return "holiday", None
            return "number", override.day_order
    except Exception:
        pass

    # 2. rr.txt fallback
    cal = _load_day_order_calendar()
    entry = cal.get(iso)
    if entry == "holiday":
        return "holiday", None
    if isinstance(entry, int):
        return "number", entry
    return "missing", None


def _weekday_index(name: str) -> int | None:
    n = name.lower()[:3]
    mapping = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    return mapping.get(n)


def _resolve_target_date_for_day_order(user_msg: str) -> date | None:
    m     = user_msg.lower()
    today = datetime.now().date()
    if "today" in m:
        return today
    if "tomorrow" in m:
        return today + timedelta(days=1)
    dm = re.search(r"\b(\d{2})-(\d{2})-(\d{4})\b", user_msg)
    if dm:
        d, mo, y = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    for w in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
        if w in m:
            idx = _weekday_index(w)
            if idx is None:
                continue
            delta = (idx - today.weekday()) % 7
            return today + timedelta(days=delta)
    if re.search(r"\b(day order|dayorder)\b", m) and not re.search(
        r"\b\d{2}-\d{2}-\d{4}\b", user_msg
    ):
        if (
            re.search(r"\b(what|which|when|how)\b.*\b(day order|dayorder)\b", m)
            or re.search(r"\b(day order|dayorder)\b.*\b(what|which|when|how)\b", m)
            or re.search(r"\b(today|now|current)\b.*\b(day order|dayorder)\b", m)
            or re.search(r"\b(day order|dayorder)\b.*\b(today|now|current)\b", m)
        ):
            return today
    return None


def _is_deterministic_day_order_query(user_msg: str) -> bool:
    m = user_msg.lower()
    if "day order" not in m and "dayorder" not in m:
        return False
    if any(k in m for k in ("timetable", "time table", "schedule", "period", "class", "classes", "what do i have", "lab")):
        return False
    return _resolve_target_date_for_day_order(user_msg) is not None


def _format_day_order_reply(student: Student, target: date) -> str:
    iso     = target.strftime("%Y-%m-%d")
    weekday = target.strftime("%A")
    kind, num = _get_day_order_for_iso(iso)
    dept = (student.department or "").strip() or "your department"
    yr   = student.year

    # Check if there's a reason in DB override
    reason_note = ""
    try:
        parsed   = date.fromisoformat(iso)
        override = DayOrderOverride.query.filter_by(date=parsed).first()
        if override and override.reason:
            reason_note = f" ({override.reason})"
    except Exception:
        pass

    if kind == "holiday":
        return (
            f"For {dept}, Year {yr}: {weekday}, {iso} is a holiday{reason_note} — "
            f"no day order on that date."
        )
    if kind == "missing":
        return (
            f"For {dept}, Year {yr}: no day order entry found for {weekday}, {iso}. "
            f"Check with your department for updates."
        )
    roman = _DAY_ORDER_ROMAN.get(num or 0, str(num))
    return (
        f"For {dept}, Year {yr}: {weekday}, {iso} is Day Order {roman} (day order {num}){reason_note}."
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Student Personalisation
# ══════════════════════════════════════════════════════════════════════════════

def _needs_student_context(user_msg: str) -> bool:
    msg = user_msg.lower()
    keywords = [
        "timetable", "time table", "schedule",
        "day order", "dayorder",
        "period", "class today",
        "what do i have", "today class",
        "lab today", "today's class"
    ]
    return any(k in msg for k in keywords)


def _get_student_context(student: Student) -> str:
    now  = datetime.now()
    dept = (student.department or "").strip() or "—"
    return (
        f"User dept: {dept}; year: {student.year}. "
        f"Date: {now.strftime('%Y-%m-%d')} ({now.strftime('%A')})."
    )


# ══════════════════════════════════════════════════════════════════════════════
#  DB Timetable helper — used in prompt when available
# ══════════════════════════════════════════════════════════════════════════════

def _get_db_timetable_for_day_order(year: int, day_order: int) -> str | None:
    """
    Returns formatted timetable string from DB if entries exist, else None.
    """
    entries = TimetableEntry.query.filter_by(
        year=year, day_order=day_order
    ).order_by(TimetableEntry.period).all()

    if not entries:
        return None

    lines = [f"Year {year} — Day Order {_DAY_ORDER_ROMAN.get(day_order, str(day_order))} Timetable:"]
    for e in entries:
        lines.append(f"  Period {e.period}: {e.subject}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  Chat Route
# ══════════════════════════════════════════════════════════════════════════════

def _register_chat_route(app: Flask):
    with app.app_context():
        _load_gemini_keys(app)

    @app.route("/chat", methods=["POST"])
    @jwt_required()
    @limiter.limit("15 per minute")
    def chat():
        global _current_key_index

        claims = get_jwt()
        if claims.get("role") == "admin":
            return jsonify({"error": "Admins cannot use the student chat endpoint"}), 403

        student_id = int(get_jwt_identity())
        student    = db.session.get(Student, student_id)
        if not student:
            return jsonify({"error": "Authenticated user not found."}), 401

        data     = request.json
        user_msg = (data or {}).get("message", "").strip()
        if not user_msg:
            return jsonify({"error": "Empty message"}), 400

        # Deterministic day order (DB override takes priority)
        if _is_deterministic_day_order_query(user_msg):
            target_date = _resolve_target_date_for_day_order(user_msg)
            if target_date:
                return jsonify({"reply": _format_day_order_reply(student, target_date)})

        # Build prompt
        relevant_chunks = _select_relevant_chunks(user_msg)
        prompt = [SYSTEM_PROMPT.strip()]

        if _needs_student_context(user_msg):
            prompt.append(_get_student_context(student))

            # If it's a timetable query, try to inject DB timetable
            msg_l = user_msg.lower()
            if any(k in msg_l for k in ("timetable", "time table", "schedule", "period", "class", "what do i have")):
                today_iso = datetime.now().date().strftime("%Y-%m-%d")
                kind, do_num = _get_day_order_for_iso(today_iso)
                if kind == "number" and do_num:
                    db_tt = _get_db_timetable_for_day_order(student.year, do_num)
                    if db_tt:
                        prompt.append(f"[DB Timetable]\n{db_tt}")
        elif _needs_date_context(user_msg):
            prompt.append(_get_current_date_context())

        if relevant_chunks:
            prompt.append("Context:")
            for c in relevant_chunks:
                prompt.append(c)

        prompt.append(f"Q: {user_msg}")

        tried_keys = 0
        last_error = None

        while tried_keys < len(_gemini_keys):
            try:
                app.logger.info(f"🚀 Trying Gemini Key-{_current_key_index + 1}")
                model    = _get_gemini_model()
                response = model.generate_content(prompt)
                return jsonify({"reply": response.text.strip()})
            except Exception as e:
                app.logger.warning("⚠️ Gemini request failed.")
                msg        = str(e).lower()
                last_error = e
                if "quota" in msg or "limit" in msg or "429" in msg:
                    _current_key_index = (_current_key_index + 1) % len(_gemini_keys)
                    tried_keys += 1
                    continue
                if "invalid key" in msg or "network" in msg:
                    _current_key_index = (_current_key_index + 1) % len(_gemini_keys)
                    tried_keys += 1
                    continue
                return jsonify({"error": str(e)}), 500

        return jsonify({
            "error": "All Gemini API keys are rate-limited. Please try again later."
        }), 429


# ══════════════════════════════════════════════════════════════════════════════
#  Entry Point
# ══════════════════════════════════════════════════════════════════════════════

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 4000))
    app.run(host="0.0.0.0", port=port)
