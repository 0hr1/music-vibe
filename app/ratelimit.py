"""Rate limits, kept in memory (the app runs as one process).

`Limiter` caps attempts per key (an IP, a username) over a sliding window: it slows down
password guessing and sign-up spam. `Throttle` spaces out our own calls to outside APIs so
all users together stay under what those services allow from one server."""

import asyncio
import time
from collections import OrderedDict, deque

import httpx


class Limiter:
    """Keeps at most `max_keys` keys, forgetting the least recently hit first, so a flood of
    new keys can't eat memory or make each hit slower."""

    def __init__(self, limit: int, window: float, max_keys: int = 10_000):
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def _recent(self, key: str) -> deque[float]:
        hits = self._hits.get(key)
        if hits is None:
            return deque()
        cutoff = time.monotonic() - self.window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            del self._hits[key]
        return hits

    def blocked(self, *keys: str) -> bool:
        return any(len(self._recent(k)) >= self.limit for k in keys)

    def hit(self, *keys: str) -> None:
        now = time.monotonic()
        for k in keys:
            hits = self._hits.get(k)
            if hits is None:
                hits = self._hits[k] = deque(maxlen=self.limit)  # older hits can't matter once it's full
            else:
                self._hits.move_to_end(k)
            hits.append(now)
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)

    def reset(self, *keys: str) -> None:
        for k in keys:
            self._hits.pop(k, None)

    def clear(self) -> None:
        self._hits.clear()


class Throttle:
    """At most one call per `interval` seconds, queued in order. Rather than queue longer than
    `max_wait`, it fails like a timeout (callers already treat that as "no data")."""

    def __init__(self, interval: float, max_wait: float = 10):
        self.interval, self.max_wait = interval, max_wait
        self._next = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        slot = max(now, self._next)
        if slot - now > self.max_wait:
            raise httpx.TimeoutException("too many requests queued")
        self._next = slot + self.interval  # claimed before sleeping, so concurrent callers queue up
        if slot > now:
            await asyncio.sleep(slot - now)


# Failed logins (also wrong "current password" on the account page). Per account, the tight limit
# counts each IP separately, so a stranger can't lock the owner out from their own address; the
# loose one caps guesses spread over many addresses.
login_by_ip = Limiter(limit=20, window=15 * 60)
login_by_user_ip = Limiter(limit=10, window=15 * 60)
login_by_user = Limiter(limit=100, window=15 * 60)
# Every sign-up attempt, successful or not (also bounds invite-code guessing)
signup_by_ip = Limiter(limit=10, window=60 * 60)


def clear_all() -> None:
    for limiter in (login_by_ip, login_by_user_ip, login_by_user, signup_by_ip):
        limiter.clear()
