"""
app.py  —  Campus AI backend
"""
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import os, re, sys, logging
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


# ══════════════════════════════════════════════════════════════
#  App Factory
# ══════════════════════════════════════════════════════════════

def create_app(env=None):
    load_dotenv()
    env = env or os.getenv("FLASK_ENV", "default")
    app = Flask(__name__)
    app.config.from_object(config_map[env])

    # ── FIX: Render gives postgres://, SQLAlchemy needs postgresql:// ──
    db_url = os.getenv("DATABASE_URL", "")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    if not db_url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")

    app.config["SQLALCHEMY_DATABASE_URI"]        = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["JWT_SECRET_KEY"]                 = os.getenv("JWT_SECRET_KEY")
    app.config["GEMINI_KEYS"]                    = os.getenv("GEMINI_KEYS")

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

    CORS(app, resources={
        r"/chat":    {"origins": "*"},
        r"/auth/*":  {"origins": "*"},
        r"/admin/*": {"origins": "*"},
    }, supports_credentials=True)

    app.register_blueprint(auth_bp,  url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    with app.app_context():
        db.create_all()
        app.logger.info("Database tables verified / created.")

    _register_chat_route(app)
    return app


# ══════════════════════════════════════════════════════════════
#  Gemini
# ══════════════════════════════════════════════════════════════

_gemini_keys = []
_cur_key     = 0
MODEL_NAME   = "gemini-2.5-flash-lite"

def _int_env(name, default):
    try: return max(1, int(os.getenv(name, "")))
    except: return default

CHUNK_MAX   = _int_env("GEMINI_CHUNK_MAX_CHARS",   1400)
CHUNK_LIMIT = _int_env("GEMINI_RETRIEVAL_CHUNKS",  2)
MAX_TOKENS  = _int_env("GEMINI_MAX_OUTPUT_TOKENS", 180)


def _load_keys(app):
    global _gemini_keys
    _gemini_keys = [k.strip() for k in app.config.get("GEMINI_KEYS","").split(",") if k.strip()]
    app.logger.info(f"Gemini keys: {len(_gemini_keys)}")
    if not _gemini_keys: raise RuntimeError("No GEMINI_KEYS set")


def _get_model():
    global _cur_key
    for _ in range(len(_gemini_keys)):
        try:
            genai.configure(api_key=_gemini_keys[_cur_key])
            return genai.GenerativeModel(MODEL_NAME,
                generation_config={"max_output_tokens": MAX_TOKENS, "temperature": 0.2})
        except Exception:
            _cur_key = (_cur_key + 1) % len(_gemini_keys)
    raise RuntimeError("All Gemini keys failed")


SYSTEM_PROMPT = (
    "You are a concise factual assistant for The New College, Chennai. "
    "'newcollege' means this college. Be brief and accurate. "
    "When a timetable is provided below, use ONLY that data — "
    "list every period in order and do not invent or omit subjects."
)


# ══════════════════════════════════════════════════════════════
#  Static chunks  (college info, fees, developers)
# ══════════════════════════════════════════════════════════════

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
        if not os.path.exists(file): continue
        with open(file, "r", encoding="utf-8") as f:
            for p in re.split(r"\n\s*\n", f.read()):
                p = p.strip()
                if len(p) > 40:
                    chunks.append({"tag": tag, "text": p.lower(), "raw": p})
    return chunks

CHUNKS = _load_chunks()

INTENT = {
    "developers": ["who made","who created","developer","built you","salman","mustansir","shahid","sathya"],
    "shift1":     ["shift 1","shift one","morning","fee","fees","fee structure","how much"],
    "shift2":     ["shift 2","shift two","evening","fee","fees","fee structure","how much"],
    "college":    ["college","new college","newcollege","about","history","principal","address","contact","department","course"],
}

def _trim(raw):
    return raw if len(raw) <= CHUNK_MAX else raw[:CHUNK_MAX-3].rstrip()+"..."

def _chunks_for(user_msg):
    ul = user_msg.lower(); kw = ul.split()
    detected = {t for t, ws in INTENT.items() if any(w in ul for w in ws)}
    scored = []
    for c in CHUNKS:
        if c["tag"] == "timetable": continue
        if detected and c["tag"] not in detected: continue
        sc = sum(1 for k in kw if k in c["text"])
        if any(w in ul for w in INTENT.get(c["tag"], [])): sc += 50
        if c["tag"] == "developers" and any(k in ul for k in INTENT["developers"]): sc += 100
        if sc > 0: scored.append((sc, c["raw"]))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [_trim(s[1]) for s in scored[:CHUNK_LIMIT]]


# ══════════════════════════════════════════════════════════════
#  Day Order  (DB override first, rr.txt fallback)
# ══════════════════════════════════════════════════════════════

ROMAN = {1:"I",2:"II",3:"III",4:"IV",5:"V",6:"VI"}

_cal = None
_CAL_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})\s+\w+\s+(.+)$")

def _rr_cal():
    global _cal
    if _cal is not None: return _cal
    _cal = {}
    path = DATA_FILES.get("timetable", "rr.txt")
    if not os.path.exists(path): return _cal
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = _CAL_RE.match(line.strip())
            if not m: continue
            dd,mm,yyyy,rest = m.group(1),m.group(2),m.group(3),m.group(4).strip().strip("`").strip()
            iso = f"{yyyy}-{mm}-{dd}"
            if rest.lower().startswith("holiday"): _cal[iso] = "holiday"
            else:
                n = re.search(r"(\d+)", rest)
                if n:
                    v = int(n.group(1))
                    if 1 <= v <= 6: _cal[iso] = v
    return _cal


def _day_order(iso):
    """Returns ('number'|'holiday'|'missing', int|None)"""
    try:
        ov = DayOrderOverride.query.filter_by(date=date.fromisoformat(iso)).first()
        if ov: return ("holiday",None) if ov.day_order==0 else ("number",ov.day_order)
    except: pass
    e = _rr_cal().get(iso)
    if e == "holiday": return ("holiday",None)
    if isinstance(e,int): return ("number",e)
    return ("missing",None)


def _wday(name):
    return {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}.get(name.lower()[:3])


def _target_date(msg):
    m, today = msg.lower(), datetime.now().date()
    if "today"    in m: return today
    if "tomorrow" in m: return today + timedelta(days=1)
    dm = re.search(r"\b(\d{2})-(\d{2})-(\d{4})\b", msg)
    if dm:
        try: return date(int(dm.group(3)),int(dm.group(2)),int(dm.group(1)))
        except: return None
    for w in ("monday","tuesday","wednesday","thursday","friday","saturday","sunday"):
        if w in m:
            idx = _wday(w)
            if idx is not None:
                return today + timedelta(days=(idx - today.weekday()) % 7)
    if re.search(r"\b(day ?order|dayorder)\b", m): return today
    return None


def _is_do_only(msg):
    m = msg.lower()
    if not re.search(r"\b(day ?order|dayorder)\b", m): return False
    if any(k in m for k in ("timetable","time table","schedule","period","class","what do i have","lab")): return False
    return True


def _do_reply(student, target):
    iso = target.isoformat(); kind,num = _day_order(iso)
    dept = (student.department or "").strip() or "your department"
    weekday = target.strftime("%A"); reason = ""
    try:
        ov = DayOrderOverride.query.filter_by(date=target).first()
        if ov and ov.reason: reason = f" ({ov.reason})"
    except: pass
    if kind=="holiday": return f"For {dept}, Year {student.year}: {weekday} {iso} is a holiday{reason} — no classes."
    if kind=="missing": return f"For {dept}, Year {student.year}: no day order found for {weekday} {iso}."
    return f"For {dept}, Year {student.year}: {weekday} {iso} is Day Order {ROMAN.get(num,str(num))}{reason}."


# ══════════════════════════════════════════════════════════════
#  Timetable extraction
# ══════════════════════════════════════════════════════════════

_rr_tt = None

def _rr_timetable():
    global _rr_tt
    if _rr_tt is not None: return _rr_tt
    _rr_tt = {}
    path = DATA_FILES.get("timetable","rr.txt")
    if not os.path.exists(path): return _rr_tt

    with open(path,"r",encoding="utf-8") as f: content = f.read()
    rom = {"I":1,"II":2,"III":3,"IV":4,"V":5,"VI":6}

    parts = re.split(r"DAY ORDER\s+(VI|V|IV|III|II|I)\b", content, flags=re.IGNORECASE)
    i = 1
    while i+1 < len(parts):
        do_num = rom.get(parts[i].strip().upper())
        block  = parts[i+1]; i += 2
        if not do_num: continue

        yr_parts = re.split(r"\b(I{1,3})\s+B\.?Sc\.?\s*\.?\s*AI\s*:", block, flags=re.IGNORECASE)
        j = 1
        while j+1 < len(yr_parts):
            yr_num = rom.get(yr_parts[j].strip().upper())
            txt    = yr_parts[j+1]; j += 2
            if not yr_num or yr_num > 3: continue

            periods = []
            for line in txt.splitlines():
                line = line.strip()
                pm = re.match(r"^([1-5])[\s:.]+(.+)", line)
                if pm:
                    subj = pm.group(2).strip()
                    if subj and not re.match(r"^(I{1,3})\s+B", subj):
                        periods.append((int(pm.group(1)), subj))
            if periods:
                _rr_tt[(yr_num, do_num)] = periods

    return _rr_tt


def _timetable_str(department, year, day_order):
    # Try DB first, filtered by department
    rows = TimetableEntry.query.filter_by(
        department=department, year=year, day_order=day_order
    ).order_by(TimetableEntry.period).all()
    if rows:
        lines = [f"{department} Year {year} — Day Order {ROMAN.get(day_order,day_order)} timetable:"]
        for r in rows: lines.append(f"  Period {r.period}: {r.subject}")
        return "\n".join(lines)
    # Fallback to rr.txt — only works for B.Sc AI
    if department == "B.Sc AI":
        periods = _rr_timetable().get((year, day_order))
        if periods:
            lines = [f"{department} Year {year} — Day Order {ROMAN.get(day_order,day_order)} timetable:"]
            for p,s in sorted(periods): lines.append(f"  Period {p}: {s}")
            return "\n".join(lines)
    return None


# ══════════════════════════════════════════════════════════════
#  Intent flags
# ══════════════════════════════════════════════════════════════

_TT  = ["timetable","time table","schedule","period","class","classes","what do i have","lab today","today's class","class today","subjects"]
_DT  = ["today","tomorrow","timetable","schedule","day order","dayorder","monday","tuesday","wednesday","thursday","friday"]

def _is_tt(msg): return any(k in msg.lower() for k in _TT)
def _needs_dt(msg): return any(k in msg.lower() for k in _DT)
def _sctx(s):
    now = datetime.now()
    return (
        f"Student: {s.name}, Dept: {(s.department or '').strip() or '—'}, "
        f"Year: {s.year}. Today: {now.strftime('%Y-%m-%d')} ({now.strftime('%A')})."
    )

# ══════════════════════════════════════════════════════════════
#  Chat route
# ══════════════════════════════════════════════════════════════

def _register_chat_route(app):
    with app.app_context():
        _load_keys(app)
        _rr_timetable()
        _rr_cal()

    @app.route("/chat", methods=["POST"])
    @jwt_required()
    @limiter.limit("15 per minute")
    def chat():
        global _cur_key
        if get_jwt().get("role") == "admin":
            return jsonify({"error": "Admins cannot use the student chat endpoint"}), 403

        student = db.session.get(Student, int(get_jwt_identity()))
        if not student: return jsonify({"error": "User not found"}), 401

        user_msg = (request.json or {}).get("message","").strip()
        if not user_msg: return jsonify({"error": "Empty message"}), 400

        if _is_do_only(user_msg):
            t = _target_date(user_msg)
            if t: return jsonify({"reply": _do_reply(student, t)})

        prompt = [SYSTEM_PROMPT]

        if _is_tt(user_msg):
            target = _target_date(user_msg) or datetime.now().date()
            iso = target.isoformat(); kind, do_num = _day_order(iso)
            weekday = target.strftime("%A")
            prompt.append(_sctx(student))

            if kind == "holiday":
                reason = ""
                try:
                    ov = DayOrderOverride.query.filter_by(date=target).first()
                    if ov and ov.reason: reason = f" ({ov.reason})"
                except: pass
                prompt.append(f"{weekday} {iso} is a holiday{reason}. No classes.")
             elif kind == "number":
                prompt.append(f"{weekday} {iso} is Day Order {ROMAN.get(do_num,do_num)}.")
                dept = (student.department or "").strip()
                tt = _timetable_str(dept, student.year, do_num)
                prompt.append(tt if tt else f"No timetable data for {dept} Year {student.year}, Day Order {ROMAN.get(do_num,do_num)}.")
            else:
                prompt.append(f"No day order found for {weekday} {iso}.")
        else:
            if _needs_dt(user_msg):
                now = datetime.now()
                prompt.append(f"Today: {now.strftime('%Y-%m-%d')} ({now.strftime('%A')}).")
            chunks = _chunks_for(user_msg)
            if chunks:
                prompt.append("Context:")
                prompt.extend(chunks)

        prompt.append(f"Q: {user_msg}")

        tried = 0
        while tried < len(_gemini_keys):
            try:
                response = _get_model().generate_content(prompt)
                return jsonify({"reply": response.text.strip()})
            except Exception as e:
                msg = str(e).lower()
                if any(k in msg for k in ("quota","limit","429","invalid key","network")):
                    _cur_key = (_cur_key+1) % len(_gemini_keys); tried += 1; continue
                return jsonify({"error": str(e)}), 500

        return jsonify({"error": "All Gemini API keys rate-limited. Try again later."}), 429


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 4000)))
