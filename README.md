# Employee Attendance & Analytics API

## Installation
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment Configuration
Set up your MongoDB connection. Create a `.env` file (which is ignored in git):
```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```
Real environment variables take precedence over `.env`.

## Startup
To start the FastAPI server:
```bash
uvicorn app.main:app --port 8000
```
Indexes are idempotently created on startup.

## Testing
To test against local fixtures or your local DB:
```bash
python sample_seed.py
```
This loads the initial documents into `employees` and `attendance_logs`. You can then interact with the endpoints.

## Known Limitations
- The `department_trend` aggregation relies partly on Python logic combined with initial MongoDB lookup for full compliance with the business rules, while maintaining the correct response structure.
- `$densify` was considered but Python date-gap filling was implemented for compatibility.
- Ensure that you use a MongoDB version >= 6.0 as specified in the assignment.
