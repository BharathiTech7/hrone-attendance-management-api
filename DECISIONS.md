# DECISIONS.md

1. **Indexes.**
Created:
- `emp_code` (unique) on `employees`: enforces unique constraint efficiently.
- `emp_code, date` (unique) on `attendance_logs`: guarantees no duplicate punch-ins for the same day and employee.
- `date, emp_code` on `attendance_logs`: optimizes listing attendance records and date-range aggregations.
- `department` on `employees`: speeds up department filtering.
Rejected: A general index on `status` alone, since filtering usually accompanies a date range or employee code where compound indexes perform better.

2. **Punch-in race.**
When two identical punch-ins arrive simultaneously, both evaluate `db.attendance_logs.insert_one(doc)`. Because of the unique compound index on `(emp_code, date)`, the database guarantees atomicity. The first insert succeeds (201), and the second throws a `DuplicateKeyError`. The application catches this error and returns a 409 Conflict.

3. **Ties.**
If employees tie on late minutes at the cutoff limit, they receive the same rank (standard competition ranking via `$setWindowFields`). The leaderboard includes all employees whose `rank <= limit`. Therefore, it returns all tied employees, potentially exceeding the `limit` count, because it accurately reflects all individuals who achieved a qualifying rank.

4. **Headcount.**
The department summary counts employees from the `employees` collection by matching `joined_on <= end_of_month` and grouping by department. It performs a `$lookup` against `attendance_logs` to get presence stats. Because the aggregation starts from the `employees` collection (not `attendance_logs`), employees with zero logs are naturally included in the headcount calculation.

5. **One thing you would change** if this had to serve 100x the data.
I would implement pre-calculated daily or monthly rollups (materialized views) rather than computing aggregations on raw logs on-the-fly.
