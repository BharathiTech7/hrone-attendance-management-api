import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def startup_db_client():
    db.employees.create_index("emp_code", unique=True)
    db.employees.create_index("department")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True)
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)])
    db.attendance_logs.create_index("emp_code")

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def to_ms(dt: Optional[datetime]) -> Optional[int]:
    if dt is None: return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

def from_ms(ms: Optional[int]) -> Optional[datetime]:
    if ms is None: return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).replace(microsecond=0)

def parse_time(t_str: str) -> tuple[int, int]:
    h, m = map(int, t_str.split(":"))
    return h, m

def is_overnight(shift_start: str, shift_end: str) -> bool:
    return parse_time(shift_end) <= parse_time(shift_start)

def get_attendance_date(punch_in_dt: datetime, shift_start: str, shift_end: str) -> str:
    pi_ist = punch_in_dt.astimezone(IST)
    d = pi_ist.date()
    if is_overnight(shift_start, shift_end):
        if pi_ist.time() < datetime.strptime(shift_end, "%H:%M").time():
            d -= timedelta(days=1)
    return d.isoformat()

def compute_late_minutes(punch_in_dt: datetime, shift_start: str, att_date: str) -> int:
    h, m = parse_time(shift_start)
    d = datetime.fromisoformat(att_date)
    start_dt = datetime(d.year, d.month, d.day, h, m, tzinfo=IST)
    diff_secs = (punch_in_dt - start_dt).total_seconds()
    if diff_secs > 600:
        return int(diff_secs // 60)
    return 0

def compute_overtime(punch_out_dt: datetime, shift_start: str, shift_end: str, att_date: str) -> int:
    h, m = parse_time(shift_end)
    d = datetime.fromisoformat(att_date)
    if is_overnight(shift_start, shift_end):
        d += timedelta(days=1)
    end_dt = datetime(d.year, d.month, d.day, h, m, tzinfo=IST)
    mins = int((punch_out_dt - end_dt).total_seconds() // 60)
    return mins if mins >= 30 else 0

def compute_work_hours(punch_in_dt: datetime, punch_out_dt: datetime) -> float:
    secs = (punch_out_dt - punch_in_dt).total_seconds()
    hrs = Decimal(str(secs)) / Decimal("3600")
    return float(hrs.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def compute_half_day(work_hours: float) -> bool:
    if work_hours is None: return False
    return work_hours < 4.50

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(..., pattern=r'^EMP\d{4,6}$')
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field("09:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: str = Field("18:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: str

    @model_validator(mode='after')
    def check_fields(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        try:
            datetime.fromisoformat(self.joined_on)
        except ValueError:
            raise ValueError("joined_on must be YYYY-MM-DD")
        return self

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    status: str = Field("PRESENT", pattern="^(PRESENT|WFH|ON_DUTY)$")

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=100000000000, le=4102444800000)

class RegularizeIn(BaseModel):
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)
    status: Optional[str] = Field(None, pattern="^(PRESENT|ABSENT|LEAVE|WFH|ON_DUTY)$")
    punch_in: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    punch_out: Optional[int] = Field(None, ge=100000000000, le=4102444800000)

# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command('ping')
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="db error")

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")

    doc.pop("_id", None)
    doc["created_at"] = to_ms(doc["created_at"])
    return doc

@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {}
    if department: q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip(skip).limit(page_size))
    for it in items:
        it["created_at"] = to_ms(it.get("created_at"))
    return {"items": items, "total": total, "page": page, "page_size": page_size}

def format_attendance_record(d: dict) -> dict:
    d.pop("_id", None)
    d["punch_in"] = to_ms(d.get("punch_in"))
    d["punch_out"] = to_ms(d.get("punch_out"))
    history = d.get("history", [])
    for h in history:
        h["at"] = to_ms(h.get("at"))
        if "punch_in" in h.get("changes", {}):
            h["changes"]["punch_in"]["from"] = to_ms(h["changes"]["punch_in"].get("from"))
            h["changes"]["punch_in"]["to"] = to_ms(h["changes"]["punch_in"].get("to"))
        if "punch_out" in h.get("changes", {}):
            h["changes"]["punch_out"]["from"] = to_ms(h["changes"]["punch_out"].get("from"))
            h["changes"]["punch_out"]["to"] = to_ms(h["changes"]["punch_out"].get("to"))
    if "half_day" not in d: d["half_day"] = False
    if "history" not in d: d["history"] = []
    return d

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp: raise HTTPException(404, "unknown employee")

    ts = from_ms(body.punched_at) if body.punched_at else datetime.now(timezone.utc).replace(microsecond=0)
    d = get_attendance_date(ts, emp["shift_start"], emp["shift_end"])

    doc = {
        "emp_code": body.emp_code, "date": d, "status": body.status,
        "punch_in": ts, "punch_out": None, "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], d),
        "overtime_minutes": 0, "half_day": False, "history": []
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return format_attendance_record(doc)

@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp: raise HTTPException(404, "unknown employee")

    ts = from_ms(body.punched_at) if body.punched_at else datetime.now(timezone.utc).replace(microsecond=0)
    log = db.attendance_logs.find_one({"emp_code": body.emp_code, "punch_in": {"$lte": ts}}, sort=[("punch_in", DESCENDING)])
    if not log: raise HTTPException(404, "no punch-in found")
    if log.get("punch_out"): raise HTTPException(409, "already punched out")
    log_pi = log["punch_in"]
    if log_pi.tzinfo is None:
        log_pi = log_pi.replace(tzinfo=timezone.utc)

    if (ts - log_pi).total_seconds() > 24 * 3600 or ts <= log_pi:
        raise HTTPException(422, "invalid punched_at time")

    wh = compute_work_hours(log_pi, ts)
    om = compute_overtime(ts, emp["shift_start"], emp["shift_end"], log["date"])
    hd = compute_half_day(wh)

    res = db.attendance_logs.find_one_and_update(
        {"_id": log["_id"], "punch_out": None},
        {"$set": {"punch_out": ts, "work_hours": wh, "overtime_minutes": om, "half_day": hd}},
        return_document=ReturnDocument.AFTER
    )
    if not res: raise HTTPException(409, "concurrent modification")
    return format_attendance_record(res)

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
    status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)
):
    q = {}
    if emp_code: q["emp_code"] = emp_code
    if date_from or date_to:
        if date_from and date_to and date_from > date_to: raise HTTPException(422, "date_from > date_to")
        q["date"] = {}
        if date_from: q["date"]["$gte"] = date_from
        if date_to: q["date"]["$lte"] = date_to
    if status: q["status"] = status

    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    docs = list(db.attendance_logs.find(q).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip(skip).limit(page_size))
    return {"items": [format_attendance_record(d) for d in docs], "total": total, "page": page, "page_size": page_size}

@app.patch("/attendance/{emp_code}/{date}", status_code=200)
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp: raise HTTPException(404, "unknown employee")
    log = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not log: raise HTTPException(404, "no record")

    new_status = body.status if body.status is not None else log["status"]
    if new_status in ("ABSENT", "LEAVE") and (body.punch_in is not None or body.punch_out is not None):
        raise HTTPException(422, "punch time with absent/leave")

    pi_val = body.punch_in if body.punch_in is not None else to_ms(log.get("punch_in"))
    po_val = body.punch_out if body.punch_out is not None else to_ms(log.get("punch_out"))

    if new_status in ("ABSENT", "LEAVE"):
        pi_val = po_val = None
    elif pi_val is None:
        raise HTTPException(422, "presence requires punch_in")

    if pi_val is not None and po_val is not None:
        if po_val <= pi_val: raise HTTPException(422, "punch_out <= punch_in")
        if (po_val - pi_val) > 24 * 3600 * 1000: raise HTTPException(422, "punch_out > 24h")

    if pi_val is not None and get_attendance_date(from_ms(pi_val), emp["shift_start"], emp["shift_end"]) != date:
        raise HTTPException(422, "punch_in not on attendance date")

    new_pi, new_po = from_ms(pi_val), from_ms(po_val)
    if new_status in ("ABSENT", "LEAVE"):
        new_wh, new_lm, new_om, new_hd = None, 0, 0, False
    else:
        new_lm = compute_late_minutes(new_pi, emp["shift_start"], date)
        if new_po is not None:
            new_wh = compute_work_hours(new_pi, new_po)
            new_om = compute_overtime(new_po, emp["shift_start"], emp["shift_end"], date)
            new_hd = compute_half_day(new_wh)
        else:
            new_wh, new_om, new_hd = None, 0, False

    changes = {}
    def chk(f, o, n):
        if o != n: changes[f] = {"from": o, "to": n}
    chk("status", log["status"], new_status)
    chk("punch_in", log.get("punch_in"), new_pi)
    chk("punch_out", log.get("punch_out"), new_po)
    chk("work_hours", log.get("work_hours"), new_wh)
    chk("late_minutes", log.get("late_minutes", 0), new_lm)
    chk("overtime_minutes", log.get("overtime_minutes", 0), new_om)
    chk("half_day", log.get("half_day", False), new_hd)

    if not changes: raise HTTPException(422, "nothing changed")

    hist_entry = {"at": datetime.now(timezone.utc).replace(microsecond=0), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    res = db.attendance_logs.find_one_and_update(
        {"_id": log["_id"]},
        {"$set": {"status": new_status, "punch_in": new_pi, "punch_out": new_po, "work_hours": new_wh, "late_minutes": new_lm, "overtime_minutes": new_om, "half_day": new_hd}, "$push": {"history": hist_entry}},
        return_document=ReturnDocument.AFTER
    )
    if not res: raise HTTPException(409, "concurrent modification")
    return format_attendance_record(res)

def get_employee_monthly_pipeline(emp_code: str, month: str):
    start_date = f"{month}-01"
    y, m = map(int, month.split('-'))
    end_date = f"{y+1}-01-01" if m == 12 else f"{y}-{m+1:02d}-01"
    return [
        {"$match": {"emp_code": emp_code}},
        {"$lookup": {
            "from": "attendance_logs", "let": {"emp": "$emp_code"},
            "pipeline": [{"$match": {"$expr": {"$eq": ["$emp_code", "$$emp"]}, "date": {"$gte": start_date, "$lt": end_date}}}],
            "as": "logs"
        }},
        {"$project": {
            "emp_code": 1, "_id": 0, "month": {"$literal": month},
            "joined_on": 1, "start_date": {"$literal": start_date}, "end_date": {"$literal": end_date},
            "logs": 1
        }}
    ]

def eval_employee_monthly(doc):
    logs = doc.get("logs", [])
    st = max(doc["start_date"], doc["joined_on"])
    ed = doc["end_date"]
    wd = 0
    curr = datetime.fromisoformat(st)
    end_dt = datetime.fromisoformat(ed)
    while curr < end_dt:
        if curr.weekday() < 5: wd += 1
        curr += timedelta(days=1)

    pd, ld = 0.0, 0
    lc, tlm, tom = 0, 0, 0
    for lg in logs:
        is_wk = datetime.fromisoformat(lg["date"]).weekday() < 5
        if lg["status"] in ("PRESENT", "WFH", "ON_DUTY"):
            if is_wk:
                pd += 0.5 if lg.get("half_day") else 1.0
        elif lg["status"] == "LEAVE":
            ld += 1

        lm = lg.get("late_minutes", 0)
        om = lg.get("overtime_minutes", 0)
        if lm > 0:
            lc += 1
            tlm += lm
        tom += om

    pct = None if wd == 0 else round(pd / wd * 100, 2)
    return {
        "emp_code": doc["emp_code"], "month": doc["month"],
        "working_days": wd, "present_days": float(pd), "leave_days": ld,
        "late_count": lc, "total_late_minutes": tlm, "total_overtime_minutes": tom,
        "attendance_pct": pct
    }

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$')):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp: raise HTTPException(404, "employee not found")
    doc = list(db.employees.aggregate(get_employee_monthly_pipeline(emp_code, month)))[0]
    return eval_employee_monthly(doc)

def get_department_summary_pipeline(month: str, department: Optional[str]):
    start_date = f"{month}-01"
    y, m = map(int, month.split('-'))
    end_date = f"{y+1}-01-01" if m == 12 else f"{y}-{m+1:02d}-01"

    match = {"joined_on": {"$lt": end_date}}
    if department: match["department"] = department

    return [
        {"$match": match},
        {"$lookup": {
            "from": "attendance_logs", "let": {"emp": "$emp_code"},
            "pipeline": [{"$match": {"$expr": {"$eq": ["$emp_code", "$$emp"]}, "date": {"$gte": start_date, "$lt": end_date}}}],
            "as": "logs"
        }},
        {"$project": {"department": 1, "logs": 1}}
    ]

@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$'), department: Optional[str] = None):
    docs = list(db.employees.aggregate(get_department_summary_pipeline(month, department)))
    depts = {}
    for d in docs:
        dept = d["department"]
        if dept not in depts:
            depts[dept] = {"headcount": 0, "pd": 0.0, "wh_sum": Decimal("0"), "wh_count": 0, "lc": 0, "tlm": 0, "lv": 0, "od": 0}

        depts[dept]["headcount"] += 1
        for lg in d.get("logs", []):
            st = lg["status"]
            if st in ("PRESENT", "WFH", "ON_DUTY"):
                if datetime.fromisoformat(lg["date"]).weekday() < 5:
                    depts[dept]["pd"] += 0.5 if lg.get("half_day") else 1.0
                wh = lg.get("work_hours")
                if wh is not None:
                    depts[dept]["wh_sum"] += Decimal(str(wh))
                    depts[dept]["wh_count"] += 1
            if st == "LEAVE": depts[dept]["lv"] += 1
            if st == "ON_DUTY": depts[dept]["od"] += 1
            lm = lg.get("late_minutes", 0)
            if lm > 0:
                depts[dept]["lc"] += 1
                depts[dept]["tlm"] += lm

    items = []
    for k in sorted(depts.keys()):
        v = depts[k]
        awh = None
        if v["wh_count"] > 0:
            awh = float((v["wh_sum"] / Decimal(v["wh_count"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        items.append({
            "department": k, "headcount": v["headcount"], "present_days": float(v["pd"]),
            "avg_work_hours": awh, "late_count": v["lc"], "total_late_minutes": v["tlm"],
            "leave_count": v["lv"], "on_duty_count": v["od"]
        })
    return {"month": month, "items": items}

def get_late_leaderboard_pipeline(month: str, department: Optional[str], limit: int):
    start_date = f"{month}-01"
    y, m = map(int, month.split('-'))
    end_date = f"{y+1}-01-01" if m == 12 else f"{y}-{m+1:02d}-01"

    match = {"date": {"$gte": start_date, "$lt": end_date}, "late_minutes": {"$gt": 0}}
    pl = [
        {"$match": match},
        {"$group": {"_id": "$emp_code", "tlm": {"$sum": "$late_minutes"}, "lc": {"$sum": 1}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "emp"}},
        {"$unwind": "$emp"}
    ]
    if department:
        pl.append({"$match": {"emp.department": department}})

    pl.extend([
        {"$sort": {"tlm": DESCENDING, "_id": ASCENDING}},
        {"$setWindowFields": {"sortBy": {"tlm": DESCENDING}, "output": {"rank": {"$denseRank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": "$emp.name", "department": "$emp.department", "total_late_minutes": "$tlm", "late_count": "$lc"}}
    ])
    return pl

@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$'), limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    items = list(db.attendance_logs.aggregate(get_late_leaderboard_pipeline(month, department, limit)))
    return {"month": month, "items": items}

def get_department_trend_pipeline(department: str, from_date: str, to_date: str):
    return [
        {"$match": {"department": department}},
        {"$lookup": {
            "from": "attendance_logs", "let": {"emp": "$emp_code"},
            "pipeline": [{"$match": {"$expr": {"$eq": ["$emp_code", "$$emp"]}, "date": {"$gte": from_date, "$lte": to_date}}}],
            "as": "logs"
        }},
        {"$project": {"emp_code": 1, "joined_on": 1, "logs": 1}}
    ]

@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str = Query(..., alias="from"), to_date: str = Query(..., alias="to")):
    if to_date < from_date: raise HTTPException(422, "to < from")
    start_dt = datetime.fromisoformat(from_date)
    end_dt = datetime.fromisoformat(to_date)
    if (end_dt - start_dt).days > 92: raise HTTPException(422, "range > 92 days")

    emp_count = db.employees.count_documents({"department": department})
    if emp_count == 0: raise HTTPException(404, "unknown department")

    docs = list(db.employees.aggregate(get_department_trend_pipeline(department, from_date, to_date)))

    days = {}
    curr = start_dt
    while curr <= end_dt:
        dstr = curr.date().isoformat()
        days[dstr] = {"date": dstr, "is_working_day": curr.weekday() < 5, "headcount": 0, "present_count": 0.0, "late_count": 0, "rate": None}
        curr += timedelta(days=1)

    for d in docs:
        jo = d["joined_on"]
        for dstr in days:
            if jo <= dstr: days[dstr]["headcount"] += 1

        for lg in d.get("logs", []):
            ldate = lg["date"]
            if ldate in days:
                st = lg["status"]
                if st in ("PRESENT", "WFH", "ON_DUTY"):
                    days[ldate]["present_count"] += 0.5 if lg.get("half_day") else 1.0
                if lg.get("late_minutes", 0) > 0:
                    days[ldate]["late_count"] += 1

    items = []
    window = []
    for dstr in sorted(days.keys()):
        v = days[dstr]
        if v["is_working_day"] and v["headcount"] > 0:
            rate = v["present_count"] / v["headcount"]
            v["rate"] = float(Decimal(str(rate)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
            window.append(v["rate"])
        else:
            window.append(None)

        ma = None
        w = [x for x in window[-7:] if x is not None]
        if w:
            m = sum(w) / len(w)
            ma = float(Decimal(str(m)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

        items.append({
            "date": v["date"], "is_working_day": v["is_working_day"], "headcount": v["headcount"],
            "present_count": float(v["present_count"]), "late_count": v["late_count"],
            "attendance_rate": v["rate"], "moving_avg_7d": ma
        })

    return {"department": department, "items": items}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str = Path(..., pattern="^(attendance_list|employee_monthly|department_summary|late_leaderboard|department_trend)$"),
    emp_code: Optional[str] = None, month: Optional[str] = Query(None, pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    department: Optional[str] = None, limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None, date_to: Optional[str] = None, status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"), to_date: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)
):
    explain = {}
    col = ""
    if endpoint == "attendance_list":
        q = {}
        if emp_code: q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from: q["date"]["$gte"] = date_from
            if date_to: q["date"]["$lte"] = date_to
        if status: q["status"] = status
        col = "attendance_logs"
        explain = db.command("explain", {"find": col, "filter": q, "sort": {"date": -1, "emp_code": 1}, "skip": (page-1)*page_size, "limit": page_size}, verbosity="executionStats")
    elif endpoint == "employee_monthly":
        if not emp_code or not month: raise HTTPException(422)
        col = "employees"
        explain = db.command("explain", {"aggregate": col, "pipeline": get_employee_monthly_pipeline(emp_code, month), "cursor": {}}, verbosity="executionStats")
    elif endpoint == "department_summary":
        if not month: raise HTTPException(422)
        col = "employees"
        explain = db.command("explain", {"aggregate": col, "pipeline": get_department_summary_pipeline(month, department), "cursor": {}}, verbosity="executionStats")
    elif endpoint == "late_leaderboard":
        if not month: raise HTTPException(422)
        col = "attendance_logs"
        explain = db.command("explain", {"aggregate": col, "pipeline": get_late_leaderboard_pipeline(month, department, limit), "cursor": {}}, verbosity="executionStats")
    elif endpoint == "department_trend":
        if not department or not from_date or not to_date: raise HTTPException(422)
        col = "employees"
        explain = db.command("explain", {"aggregate": col, "pipeline": get_department_trend_pipeline(department, from_date, to_date), "cursor": {}}, verbosity="executionStats")

    return {"endpoint": endpoint, "collection": col, "explain": explain}
