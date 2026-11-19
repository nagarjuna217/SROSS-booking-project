# Decisions
---

## 1. A user who already holds a seat tries to hold a second one for the same showtime

**Decision:** It's blocked.A user can hold only one seat per showtime at a time. If they try to hold a second seat, they get an error that tells them which seat they’re already holding. 

- After they confirm that seat, it becomes a booking, so they can go ahead and hold another seat.
- If they cancel the hold, or let it run out, they can also hold a new seat.
- If they accidentally send the same request twice (same seat), that’s fine. They just get their existing hold back, not an error.
**Alternatives considered:**
- Allow up to N holds per user (for example 4, for groups).

**Why:** Holding a seat is free and blocks it for everyone else for 2 minutes. In a small room, one person holding many seats could block the whole show without paying for anything.

There’s also a technical bonus: “one hold per user” is a rule the database itself can enforce, so it holds even if someone sends many requests at exactly the same moment. A limit like “4 per user” can’t be enforced by the database as easily; it would need extra locking code.

The downside: a group has to book one seat at a time. I’ve listed this as a known limitation..

---

## 2. A hold is placed when the showtime is less than 2 minutes away

**Decision:** The hold is **allowed, but it expires at the showtime's start**:

`hold_expires_at = min(now + 2 minutes, starts_at)`

For example, if the show starts in 40 seconds, the hold lasts 40 seconds. Once the show has started, no new holds are allowed.

**Alternatives considered:**
- Not allowing any holds in the last 2 minutes.
-Giving the full 2 minutes anyway, so the hold could run past the start time.

**Why:** People do turn up at the last minute, and refusing them loses a sale for no reason. But a hold that lasts past the start time makes no sense, because it blocks a seat for a show that’s already running. Cutting the hold off at the start time is the fair middle ground. The response tells the user exactly when their hold ends, so they know how much time they have.

---

## 3. Should a cancelled reservation's seat become available immediately, or go through a cooldown?

**Decision:** **Immediately.** As soon as the cancellation goes through, anyone can hold that seat.

**Alternatives considered:**
- A cooldown period or waiting period of a few minutes before the seat goes back on sale. That would stop people from cancelling and instantly grabbing the seat again to cheat the system, and would give time for a refund to process.

**Why:**
- Most cancellations happen close to showtime, which is exactly when seats are hardest to find. A waiting period would hide seats at the worst possible moment.

- A waiting period would add extra complexity everywhere seats are counted.


---

## 4. HTTP status and body for "hold expired between your list call and your confirm call"

**Decision:** **`410 Gone`**, with code `hold_expired`, plus `expired_at` and the seat:

```json
{
  "error": {
    "code": "hold_expired",
    "message": "Your hold on this seat has expired. Place a new hold to continue.",
    "details": {
      "reservation_id": "f6ba33e1-8da6-440c-b6c4-88a2a44f6c5a",
      "expired_at": "2026-11-18T20:05:39.985785+00:00",
      "seat": { "id": 5, "label": "A5" }
    }
  }
}
```

The same response is returned whether or not the sweeper has already flipped the row to `EXPIRED`. Expiry is decided by the timestamp, not by the sweeper.

**Alternatives considered:**
- `409 Conflict`.
- `422 Unprocessable Entity`.
- `404`, treating the expired hold as if it no longer existed.

**Why:** The hold did exist and is now permanently gone. Retrying the same request can never succeed, which is exactly what 410 means.

It also keeps 409 free for "someone else has this seat" (`seat_unavailable`). Those two cases need different client behaviour:

- **410**:"your time ran out. Try holding the same seat again; it may still be free".
- **409**:"someone else took it. pick a different seat".
- **404**: would be misleading, because the reservation still exists and you can still look it up. It just shows as expired.
- Including the seat lets the client try again straight away, without fetching the seat list again including the expiry time lets it tell the user what happened

