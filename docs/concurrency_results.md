# Concurrency results

This is the output of [`scripts/concurrency_check.py`](../scripts/concurrency_check.py), captured from the docker-compose stack. The API ran under gunicorn with 4 workers × 4 threads, so up to 16 requests were processed in parallel, against PostgreSQL 16.

## How to reproduce

```bash
docker compose up --build -d
docker compose run --rm migrate python manage.py seed_demo --reset
python3 scripts/concurrency_check.py -n 20
```

All N requests in a round wait on a `threading.Barrier` and are released together. The script prints how far apart they actually started, which was under 1 ms in this run.

## Output (20 concurrent requests per round)

```
Using showtime 1, concurrency 20

[1] 20 different users hold seat A1 (id=1) simultaneously
    requests released within 0.8 ms of each other
    HTTP statuses: {409: 19, 201: 1}   error codes: {'seat_unavailable': 19}
    [PASS] exactly one hold succeeded
    [PASS] the other 19 got 409 seat_unavailable
    winner: racer-8ae3c6-14, reservation d4fa9e0f-8d9e-4dee-9c06-5faed16fba1e

[2] winner sends 20 confirm requests simultaneously (retry storm)
    requests released within 0.6 ms of each other
    HTTP statuses: {200: 20}   Idempotent-Replay: {'true': 19, 'false': 1}
    [PASS] all 20 confirms returned 200
    [PASS] exactly one confirm changed state, the rest were replays
    [PASS] every response describes the same CONFIRMED reservation
    [PASS] seat A1 now shows as booked
    [PASS] a loser confirming the winner's hold gets 403 (403 reservation_not_owned)

[3] one user (greedy-8ae3c6) holds 20 different seats simultaneously
    requests released within 0.9 ms of each other
    HTTP statuses: {409: 19, 201: 1}   error codes: {'user_already_holding': 19}
    [PASS] exactly one hold succeeded
    [PASS] the other 19 got 409 user_already_holding

Summary before: {'showtime_id': 1, 'total_seats': 50, 'booked': 0, 'held': 0, 'available': 50, 'as_of': '2026-11-18T20:09:48.410397Z'}
Summary after:  {'showtime_id': 1, 'total_seats': 50, 'booked': 1, 'held': 1, 'available': 48, 'as_of': '2026-11-18T20:09:48.501911Z'}
    [PASS] booked count went up by exactly 1
    [PASS] booked + held + available == total_seats

RESULT: ALL CHECKS PASSED
```

## Checking the database directly

The API could, in theory, report one winner while two rows were written. So I checked the table itself after the run.

There is exactly one live row for seat A1, the confirmed booking. The single `HELD` row is the one hold that won round 3.

```
 seat |  status   | count 
------+-----------+-------
 A1   | CONFIRMED |     1
 A7   | HELD      |     1
(2 rows)

```

No (showtime, seat) pair has more than one live reservation:

```
 showtime_id | seat_id | count 
-------------+---------+-------
(0 rows)

```

## The database refuses a duplicate even without the API

The partial unique index is the actual guarantee. To show it doesn't depend on the application's checks or locks, I inserted a second live row for a booked seat directly with SQL. It was rejected:

```sql
INSERT INTO booking_reservation (id, showtime_id, seat_id, user_id, status, hold_expires_at, created_at, updated_at)
SELECT gen_random_uuid(), showtime_id, seat_id, 'sneaky-direct-sql', 'HELD', now() + interval '2 minutes', now(), now()
FROM booking_reservation WHERE status = 'CONFIRMED' LIMIT 1;
```

```
ERROR:  duplicate key value violates unique constraint "uniq_active_reservation_per_seat"
DETAIL:  Key (showtime_id, seat_id)=(1, 1) already exists.
```

## Repeatability

I ran the script repeatedly, with `-n 10` and `-n 20`, both against the compose stack and against gunicorn running on the host. Every run passed. Each run uses fresh user ids and picks the next available seats, so it can be re-run without resetting the data, until the room fills up.
