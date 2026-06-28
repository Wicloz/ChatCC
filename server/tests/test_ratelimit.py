from ratelimit import RateLimiter, TokenBucket
from _util import Clock


def test_bucket_allows_up_to_capacity_then_denies():
    clock = Clock()
    b = TokenBucket(capacity=3, refill_per_sec=1, clock=clock)
    assert b.allow() and b.allow() and b.allow()  # 3 in the burst
    assert not b.allow()                           # 4th denied (no time passed)


def test_bucket_refills_over_time():
    clock = Clock()
    b = TokenBucket(capacity=2, refill_per_sec=1, clock=clock)
    assert b.allow() and b.allow()
    assert not b.allow()
    clock.advance(1.0)          # one token regained
    assert b.allow()
    assert not b.allow()


def test_bucket_refill_caps_at_capacity():
    clock = Clock()
    b = TokenBucket(capacity=2, refill_per_sec=1, clock=clock)
    clock.advance(100)          # idle a long time
    assert b.allow() and b.allow()
    assert not b.allow()        # never accrues more than capacity


def test_retry_after():
    clock = Clock()
    b = TokenBucket(capacity=1, refill_per_sec=0.5, clock=clock)  # 1 per 2s
    assert b.allow()
    assert b.retry_after() == 2.0   # need to wait 2s for the next token
    clock.advance(1.0)
    assert b.retry_after() == 1.0
    assert not b.allow()            # still empty at +1s


def test_limiter_keys_are_isolated():
    clock = Clock()
    rl = RateLimiter(capacity=1, refill_per_sec=1, clock=clock)
    assert rl.allow("alice")
    assert not rl.allow("alice")     # alice exhausted
    assert rl.allow("bob")           # bob unaffected
