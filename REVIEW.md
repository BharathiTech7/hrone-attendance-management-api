# REVIEW.md

| # | Where (function / line) | What is wrong | How you'd notice it | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Fails on overnight shifts / truncates improperly | 09:40:01 calculates as 10 late, but shift on next day breaks | Constructed proper IST datetime for shift start and used floor division |
| 2 | `compute_work_hours` | Uses float rounding instead of Decimal half-up | 4.495 rounds to 4.49 (bankers) instead of 4.50 | Replaced with `Decimal.quantize` using `ROUND_HALF_UP` |
| 3 | `compute_overtime` | Incorrect date for overnight shifts | Overtime on 22:00-06:00 shift calculates negatively | Added `timedelta(days=1)` for overnight shift end times |
| 4 | `create_employee` / 82 | Race condition and naive datetime | Two identical requests get 201; timestamps are wrong | Used unique index, caught `DuplicateKeyError`, used UTC |
| 5 | `list_employees` / 93 | Incorrect pagination skip logic | Page 1 skips the first 20 items | Changed to `(page - 1) * page_size` |
| 6 | `punch_in` / 101 | 500 error if employee not found | Unknown emp_code raises TypeError | Added `if not emp: raise HTTPException(404)` |
| 7 | `punch_in` / 102 | Uses naive local timestamp / incorrect logic | Overrides tz to local | Converted from epoch ms to UTC datetime |
| 8 | `punch_in` / 103 | Attendance date ignores overnight shifts | 03:00 punch for 22:00 shift gets wrong day | Replaced with `get_attendance_date` logic checking shift bounds |
| 9 | `punch_in` / 104 | Race condition for check-then-insert | Concurrent punch-ins create duplicates | Rely on unique index `(emp_code, date)` and catch exception |
| 10 | `list_attendance` / 144 | Loads entire collection into memory | OOM on 100k records | Used MongoDB `skip().limit()` and `.sort()` on cursor |

*Note on non-defects*: I inspected the choice to use `BaseModel` for validation and decided it was correct, though I tightened constraints. The use of FastAPI defaults was acceptable.
