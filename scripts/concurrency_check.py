#!/usr/bin/env python3
"""
Concurrency check for the seat booking API.

Fires genuinely simultaneous HTTP requests at a running server and asserts
the invariants hold:

  1. N different users try to hold the SAME seat at the same instant
     -> exactly one 201, everyone else gets 409 seat_unavailable.
  2. The winner sends N confirm requests at once (a client retrying after
     timeouts) -> all return 200, exactly one actually confirms, and the
     seat ends up booked exactly once.
  3. ONE user tries to hold N DIFFERENT seats at once
     -> exactly one 201, the rest get 409 user_already_holding.

Requests in each round are released together by a threading.Barrier, so
they hit the server within a few milliseconds of each other. Run the server
with multiple workers (e.g. gunicorn -w 4 --threads 4) so requests are
actually processed in parallel.

Standard library only:

    python scripts/concurrency_check.py --base-url http://localhost:8000 --showtime 1 -n 20
"""
import argparse
import json
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib import error, request


def call(method: str, url: str, user: str | None = None, body: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if user:
        headers["X-User-Id"] = user
    data = json.dumps(body).encode() if body is not None else None
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read() or b"null")
    except error.HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read() or b"null")


def fire_together(jobs):
    """Run callables in parallel, releasing them all at the same moment."""
    barrier = threading.Barrier(len(jobs))

    def run(job):
        barrier.wait()
        started = time.perf_counter()
        result = job()
        return result, started

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        results = list(pool.map(run, jobs))
    starts = [s for _, s in results]
    spread_ms = (max(starts) - min(starts)) * 1000
    return [r for r, _ in results], spread_ms


def error_code(payload):
    return (payload or {}).get("error", {}).get("code")


class Check:
    def __init__(self):
        self.failures = 0

    def expect(self, condition: bool, message: str):
        print(f"    [{'PASS' if condition else 'FAIL'}] {message}")
        if not condition:
            self.failures += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--showtime", type=int, default=1)
    parser.add_argument("-n", "--concurrency", type=int, default=20)
    args = parser.parse_args()

    api = args.base_url.rstrip("/") + "/api/v1"
    n = args.concurrency
    run_id = uuid.uuid4().hex[:6]  # keeps user ids unique across runs
    check = Check()

    status, _, data = call("GET", f"{api}/showtimes/{args.showtime}/seats/?status=available")
    if status != 200:
        sys.exit(f"Could not list seats ({status}): {data}")
    available = data["seats"]
    if len(available) < n + 1:
        sys.exit(f"Need at least {n + 1} available seats, found {len(available)}. "
                 f"Run: python manage.py seed_demo --reset")

    _, _, before = call("GET", f"{api}/showtimes/{args.showtime}/summary/")
    target = available[0]

    # ------------------------------------------------------------------ 1
    print(f"\n[1] {n} different users hold seat {target['label']} (id={target['id']}) simultaneously")
    users = [f"racer-{run_id}-{i:02d}" for i in range(n)]
    results, spread = fire_together([
        (lambda u=u: (u, call("POST", f"{api}/showtimes/{args.showtime}/holds/", u, {"seat_id": target["id"]})))
        for u in users
    ])
    print(f"    requests released within {spread:.1f} ms of each other")
    statuses = Counter(r[1][0] for r in results)
    codes = Counter(error_code(r[1][2]) for r in results if r[1][0] != 201)
    print(f"    HTTP statuses: {dict(statuses)}   error codes: {dict(codes)}")
    winners = [(u, resp) for u, resp in results if resp[0] == 201]
    check.expect(len(winners) == 1, "exactly one hold succeeded")
    check.expect(statuses[409] == n - 1 and codes == {"seat_unavailable": n - 1},
                 f"the other {n - 1} got 409 seat_unavailable")
    if len(winners) != 1:
        sys.exit("Aborting: invariant broken in round 1.")
    winner, (_, _, hold) = winners[0]
    print(f"    winner: {winner}, reservation {hold['id']}")

    # ------------------------------------------------------------------ 2
    print(f"\n[2] winner sends {n} confirm requests simultaneously (retry storm)")
    results, spread = fire_together([
        (lambda: call("POST", f"{api}/reservations/{hold['id']}/confirm/", winner)) for _ in range(n)
    ])
    print(f"    requests released within {spread:.1f} ms of each other")
    statuses = Counter(s for s, _, _ in results)
    replays = Counter(h.get("Idempotent-Replay") for s, h, _ in results if s == 200)
    print(f"    HTTP statuses: {dict(statuses)}   Idempotent-Replay: {dict(replays)}")
    check.expect(statuses == {200: n}, f"all {n} confirms returned 200")
    check.expect(replays.get("false") == 1, "exactly one confirm changed state, the rest were replays")
    check.expect(len({b["id"] for _, _, b in results}) == 1 and all(b["status"] == "CONFIRMED" for _, _, b in results),
                 "every response describes the same CONFIRMED reservation")

    _, _, seats = call("GET", f"{api}/showtimes/{args.showtime}/seats/")
    seat_state = next(s for s in seats["seats"] if s["id"] == target["id"])
    check.expect(seat_state["status"] == "booked", f"seat {target['label']} now shows as booked")

    loser = next(u for u in users if u != winner)
    status, _, body = call("POST", f"{api}/reservations/{hold['id']}/confirm/", loser)
    check.expect(status == 403 and error_code(body) == "reservation_not_owned",
                 f"a loser confirming the winner's hold gets 403 ({status} {error_code(body)})")

    # ------------------------------------------------------------------ 3
    seats_for_one_user = available[1:n + 1]
    solo = f"greedy-{run_id}"
    print(f"\n[3] one user ({solo}) holds {n} different seats simultaneously")
    results, spread = fire_together([
        (lambda s=s: call("POST", f"{api}/showtimes/{args.showtime}/holds/", solo, {"seat_id": s["id"]}))
        for s in seats_for_one_user
    ])
    print(f"    requests released within {spread:.1f} ms of each other")
    statuses = Counter(s for s, _, _ in results)
    codes = Counter(error_code(b) for s, _, b in results if s != 201)
    print(f"    HTTP statuses: {dict(statuses)}   error codes: {dict(codes)}")
    check.expect(statuses[201] == 1, "exactly one hold succeeded")
    check.expect(codes == {"user_already_holding": n - 1}, f"the other {n - 1} got 409 user_already_holding")

    # ------------------------------------------------------------- summary
    _, _, after = call("GET", f"{api}/showtimes/{args.showtime}/summary/")
    print(f"\nSummary before: {before}")
    print(f"Summary after:  {after}")
    check.expect(after["booked"] - before["booked"] == 1, "booked count went up by exactly 1")
    check.expect(after["booked"] + after["held"] + after["available"] == after["total_seats"],
                 "booked + held + available == total_seats")

    print("\nRESULT:", "ALL CHECKS PASSED" if check.failures == 0 else f"{check.failures} CHECK(S) FAILED")
    sys.exit(1 if check.failures else 0)


if __name__ == "__main__":
    main()
