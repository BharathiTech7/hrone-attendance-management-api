# Employee Attendance & Analytics API

A production-ready RESTful backend built with Python, FastAPI, and MongoDB for employee attendance tracking, regularization workflows, and organizational analytics. The API enforces strict schema validation, handles concurrency race conditions atomically at the database level, and provides administrative explain-plan inspection for performance observability.

---

## Features

- **Employee Management:** Create and list employees with department filtering, deterministic sorting, pagination, and shift definition (`shift_start`, `shift_end`).
- **Atomic Punch-In / Punch-Out:** 
  - Prevents duplicate punch-ins using a unique compound index on `(emp_code, date)`.
  - Atomic single-winner punch-out using MongoDB `find_one_and_update` on unpunched records, eliminating concurrent double punch-out race conditions.
- **Attendance Corrections & Audit Trail:** Regularize attendance records (`PATCH /attendance/{emp_code}/{date}`) with historical change logs (`history`) tracking timestamp, modifier, reason, and field-level diffs (`from` / `to`).
- **Filtering & Pagination:** Standardized pagination (`page`, `page_size`, `total`, `items`) on employee listings and attendance logs with date-range and status filters.
- **Organizational Analytics:**
  - Individual monthly attendance summaries (working days, present days, leave days, late count, total late minutes, overtime, attendance percentage).
  - Department summaries aggregating headcount, total working days, average attendance rate, and punctuality percentages.
  - Department 90-day attendance trends with continuous calendar date densification and 7-day moving averages.
  - Monthly late arrival leaderboards ranked by total late minutes and late occurrences.
- **Database Indexing & Explain Plans:** Idempotent compound and single-field index initialization on application startup, paired with an administrative `/admin/explain/{endpoint}` route providing query execution statistics (`executionStats`).

---

## Technology Stack

- **Language:** Python 3.11+
- **Framework:** FastAPI
- **ASGI Server:** Uvicorn
- **Database Driver:** PyMongo (>= 4.5.0)
- **Database:** MongoDB 6.0+ (Tested on MongoDB 8.0.9)
- **Data Validation:** Pydantic v2
- **Environment Management:** python-dotenv

---

## Prerequisites

- **Python:** Version 3.11 or higher
- **MongoDB:** Version 6.0 or higher running locally (e.g., `mongodb://127.0.0.1:27017`) or accessible via MongoDB Atlas URI

---

## Installation and Setup

### 1. Clone & Navigate to Repository
Open your terminal in the repository root directory.

### 2. Create and Activate Virtual Environment

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux / macOS (Bash/Zsh):**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

---

## MongoDB Configuration

The application loads configuration via `python-dotenv` in `app/main.py`. It prioritizes system environment variables, followed by a local `.env` file, and falls back to standard local defaults:

- `MONGO_URI` (default: `mongodb://localhost:27017`)
- `MONGO_DB` (default: `attendance_db`)

For standard unauthenticated local MongoDB instances, no `.env` file is required. If connecting to a custom port, authenticated instance, or MongoDB Atlas, create an optional `.env` file in the project root:

```env
MONGO_URI=mongodb://127.0.0.1:27017
MONGO_DB=attendance_db
```

> **Security Note:** The `.env` file is explicitly excluded by `.gitignore` and must never be committed to source control. Local unauthenticated MongoDB instances do not require credentials.

---

## Running the Application

Start the API server using Uvicorn from the repository root:

```bash
uvicorn app.main:app --port 8000
```

Once the server is running, the interactive documentation and health check are accessible at:
- **Interactive Swagger UI:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc Documentation:** [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)
- **Health Check:** [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

---

## API Endpoint Reference

All endpoints conform to the contracts defined in `openapi.yaml`.

### Employee Management
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/employees` | Register a new employee with unique `emp_code`, department, and shift timings (201 Created, 409 Conflict). |
| `GET` | `/employees` | List employees with optional department filter and pagination (`page`, `page_size`). |

### Attendance Tracking & Regularization
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/attendance/punch-in` | Record a punch-in event. Maps to attendance date based on shift and checks duplicate punch-ins (201 Created, 409 Conflict). |
| `POST` | `/attendance/punch-out` | Record punch-out for the most recent unclosed punch-in, calculating work hours, overtime, and half-day status (200 OK, 404, 409, 422). |
| `GET` | `/attendance` | Query attendance logs filtered by `emp_code`, `date_from`, `date_to`, and `status` with pagination. |
| `PATCH` | `/attendance/{emp_code}/{date}` | Regularize attendance record, recalculating metrics and appending an entry to the audit `history` (200 OK, 404, 409, 422). |

### Analytics
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/analytics/employees/{emp_code}/monthly` | Monthly employee summary: working days, present days, leaves, late count, late minutes, overtime, attendance %. |
| `GET` | `/analytics/departments/summary` | Aggregated department monthly summary across all active employees. |
| `GET` | `/analytics/departments/{department}/trend` | Date-range attendance trend with calendar day densification and 7-day moving averages (max 92 days). |
| `GET` | `/analytics/late-leaderboard` | Top late arrivals for a given month, ranked by total late minutes and occurrence count. |

### Administration & Diagnostics
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Liveness check verifying MongoDB connectivity (`ping` command) (200 OK, 503 Service Unavailable). |
| `GET` | `/admin/explain/{endpoint}` | Returns MongoDB execution plan (`executionStats`) for queries and aggregations across supported routes. |

---

## Sample Data & Seeding

The repository provides nine reference documents in `sample_data/` demonstrating document shapes.

To load the sample data:
```bash
python sample_seed.py
```

> **Warning:** Inspecting `sample_seed.py` confirms that it executes `db[name].delete_many({})` prior to insertion. Running this script **resets and clears existing data** in both the `employees` and `attendance_logs` collections in the target database.

---

## Database Design & Performance

### Collections & Schema
The application operates on two primary collections:
1. **`employees`**: Stores employee profiles, department assignments, joining dates, and shift configurations (`shift_start`, `shift_end`).
2. **`attendance_logs`**: Stores daily attendance records with millisecond epoch timestamps for external serialization and native BSON datetimes for internal querying, status, work hours, late/overtime minutes, half-day boolean flags, and regularization `history`.

### Indexes Created on Startup
Indexes are initialized idempotently during application startup in `app/main.py`:
- `employees`:
  - `emp_code` (Unique index)
  - `department` (Single-field index)
- `attendance_logs`:
  - `(emp_code, date)` (Unique compound index — enforces single daily punch-in record)
  - `(date DESC, emp_code ASC)` (Compound index — accelerates date-range filtering and pagination)
  - `emp_code` (Single-field index)

### Explain Plan Inspection
The `/admin/explain/{endpoint}` endpoint allows direct inspection of query plans using MongoDB's `explain(verbosity="executionStats")`. This confirms whether query filters utilize index scans (`IXSCAN`) or collection scans (`COLLSCAN`), providing runtime visibility into query performance.

---

## Business Rules Summary

- **Overnight Shifts:** Shifts where `shift_end <= shift_start` (e.g., 22:00 to 06:00) span two calendar days. A punch-in before shift end is attributed to the prior calendar day.
- **Late Arrivals & Grace Period:**
  - Standard grace period is 10 minutes past scheduled `shift_start`.
  - Punch-in within 10 minutes (`diff_secs <= 600`) results in `late_minutes = 0`.
  - Punch-in after 10 minutes (`diff_secs > 600`) records the full difference from scheduled start time in whole minutes (`diff_secs // 60`).
- **Half-Day Threshold:**
  - Total work hours strictly less than 4.50 hours (`work_hours < 4.50`) evaluates to `half_day = True`.
  - Exactly 4.50 hours or greater evaluates to `half_day = False`.
  - In monthly analytics, half-day attendance contributes 0.5 to total present days.
- **Overtime Calculation:** Overtime begins 30 minutes after scheduled shift end. If extra work is less than 30 minutes, `overtime_minutes = 0`. If 30 minutes or more, the full duration beyond shift end is credited.
- **Hour Rounding:** Work hours are calculated by converting duration in seconds to hours with decimal arithmetic and rounding to two decimal places using standard half-up rounding (`ROUND_HALF_UP`).
- **Concurrency Protection:**
  - Duplicate punch-ins are caught via the unique `(emp_code, date)` index and handled with `409 Conflict`.
  - Concurrent punch-outs use atomic conditional updates (`{"_id": log["_id"], "punch_out": None}`) ensuring only one request succeeds (200 OK) while simultaneous attempts receive `409 Conflict`.
- **Regularization Validation:**
  - Changing status to `ABSENT` or `LEAVE` clears punch timestamps.
  - Setting presence requires a valid `punch_in`.
  - Regularized `punch_out` must be greater than `punch_in` and cannot exceed 24 hours.
  - Every update logs an entry into the record's `history` array.

---

## Testing & Verification

- **Code Compilation:** Validated using `python -m py_compile app/main.py`.
- **Pytest Discovery:** `python -m pytest -q` reports that no test suites are included in the repository root (`no tests ran`).
- **Observed Automated Verification:**
  - Pre-submission audit scripts were executed against an isolated local database (`attendance_presubmission_isolated_test_db`) connected to MongoDB 8.0.9.
  - **Concurrency:** Multithreaded execution confirmed atomic single-winner behavior on concurrent punch-outs (one 200, one 409) and concurrent regularizations.
  - **Boundaries:** Verified exact 4.50-hour half-day threshold, late grace boundaries (10m vs 11m), and overtime thresholds.
  - **Explain Plans:** Verified `/admin/explain/{endpoint}` executes successfully across all supported endpoints.
- **Performance Scale Note:** Query execution paths and index usage have been confirmed via explain plans (`IXSCAN`). Physical dataset population of 100,000 synthetic records was not conducted in the local database.

---

## Design Decisions & Known Limitations

As documented in `DECISIONS.md` and `REVIEW.md`:
1. **Single File Architecture:** All application logic, Pydantic models, helper functions, and endpoint handlers reside in `app/main.py` per assignment specification.
2. **Trend Analytics Densification:** In `GET /analytics/departments/{department}/trend`, missing dates across the requested window are densified in Python rather than relying on MongoDB `$densify` to maximize compatibility across deployment environments.
3. **Optimistic Locking on Punch-Out:** Rather than database-level transaction locks, an atomic filter query (`{"_id": log["_id"], "punch_out": None}`) is used with `find_one_and_update` to handle concurrency with low overhead.
4. **Timezone Standardization:** The API normalizes all internal calculations against Indian Standard Time (IST, UTC+05:30) while storing UTC datetimes in BSON and outputting millisecond epoch timestamps where required by the OpenAPI contract.

---

## Repository Structure

```text
candidate_kit/
├── app/
│   ├── __init__.py
│   └── main.py              # Complete FastAPI application and endpoints
├── sample_data/
│   ├── attendance_logs.json # Reference document shapes for attendance
│   └── employees.json       # Reference document shapes for employees
├── .gitignore               # Excludes .env, virtual environments, pycache
├── DATA_MODEL.md            # MongoDB document structure and field definitions
├── DECISIONS.md             # Architectural decisions and rationale
├── openapi.yaml             # Official OpenAPI 3.0 API specification
├── PROBLEM_STATEMENT.docx   # Candidate kit assignment requirements
├── README.md                # Project documentation and evaluator guide
├── requirements.txt         # Project dependencies
├── REVIEW.md                # Self-audit findings and bug fixes
└── sample_seed.py           # Database seeder for sample documents
```

---

## Submission & Verification Summary

To start the API and test endpoints:
1. Ensure MongoDB is running locally (`127.0.0.1:27017`).
2. Run `uvicorn app.main:app --port 8000`.
3. Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) to explore and execute API requests interactively.
