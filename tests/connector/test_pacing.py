"""Claim-poll pacing: defaults are unchanged, idle back-off cuts traffic, hot window keeps latency low, worst-case pickup is bounded.

The latency figures here are from a DETERMINISTIC SIMULATION with an injected clock (no network, no Lambda). They bound the pacer's own
behaviour; real end-to-end latency (HTTP round-trip, cold starts) must be measured on a deployed stack before any latency claim is published.
"""
from __future__ import annotations

import random

import pytest

from enigma_connector.config import Config, ConfigError
from enigma_connector.pacing import ClaimPacer

TOKEN = "enig_rt_abcdefgh_" + "A" * 43


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def simulate(fast, idle, hot, *, duration, job_at=(), seed=1):
    """Poll loop on a fake clock. Returns (claim_count, pickup_latencies). A job becomes claimable at each time in job_at and is picked up by the next poll."""
    clk, rng = Clock(), random.Random(seed)
    p = ClaimPacer(fast, idle, hot, clock=clk, rand=rng.random)
    pending, polls, lat = sorted(job_at), 0, []
    while clk.t < duration:
        polls += 1
        if pending and pending[0] <= clk.t:
            lat.append(clk.t - pending.pop(0))
            p.on_activity()
            clk.t += 0.5                                                                      # time to run a (short) job
            continue
        clk.t += p.next_wait(1.0)
    return polls, lat


def test_default_is_exactly_the_old_fixed_rate_poll():
    clk = Clock()
    p = ClaimPacer(1.0, 0.0, 30.0, clock=clk)
    assert [p.next_wait(1.0) for _ in range(5)] == [1.0] * 5                                  # no jitter, no back-off, server hint honoured as a floor
    clk.t = 10_000
    assert p.next_wait(1.0) == 1.0 and p.next_wait(2.5) == 2.5                                # hint above the rate still wins


def test_idle_backoff_ramps_then_caps_with_bounded_jitter():
    clk = Clock()
    p = ClaimPacer(1.0, 5.0, 30.0, clock=clk, rand=lambda: 0.5)                               # rand 0.5 => zero jitter
    clk.t = 100                                                                               # long idle
    waits = [p.next_wait(0.0) for _ in range(8)]
    assert waits[0] == pytest.approx(1.5) and waits == sorted(waits) and max(waits) == pytest.approx(5.0)
    hi = ClaimPacer(1.0, 5.0, 30.0, clock=clk, rand=lambda: 1.0)
    clk.t += 1000
    assert all(hi.next_wait(0.0) <= 5.5 + 1e-9 for _ in range(10))                            # at most +10 % jitter


def test_hot_window_after_start_and_after_a_job_polls_fast():
    clk = Clock()
    p = ClaimPacer(1.0, 5.0, 30.0, clock=clk, rand=lambda: 0.5)
    assert p.next_wait(0.0) == 1.0                                                            # just started: hot
    clk.t = 100
    assert p.next_wait(0.0) > 1.0                                                             # idle: backing off
    p.on_activity()
    assert p.next_wait(0.0) == 1.0                                                            # a job just ran: hot again
    clk.t += 31
    assert p.next_wait(0.0) > 1.0


def test_idle_backoff_cuts_claim_traffic_by_most_of_the_idle_requests():
    base, _ = simulate(1.0, 0.0, 30.0, duration=3600)
    tuned, _ = simulate(1.0, 5.0, 30.0, duration=3600)
    assert base >= 3500
    assert tuned < base * 0.25                                                                # 5 s ceiling: roughly 80 % fewer idle claims


def test_simulated_pickup_latency_is_bounded_by_the_idle_ceiling():
    rng = random.Random(7)
    jobs = sorted(rng.uniform(100, 3500) for _ in range(150))
    _, lat = simulate(1.0, 5.0, 30.0, duration=3600, job_at=jobs, seed=3)
    assert len(lat) == 150
    lat.sort()
    p50, p95, worst = lat[len(lat) // 2], lat[int(len(lat) * 0.95)], lat[-1]
    assert worst <= 5.5 + 1e-9                                                                # ceiling 5 s + 10 % jitter, never more
    assert p50 < 3.5 and p95 <= 5.5
    _, fast_lat = simulate(1.0, 0.0, 30.0, duration=3600, job_at=jobs, seed=3)
    assert max(fast_lat) <= 1.0 + 1e-9                                                        # the unchanged default picks up within one fast interval


def test_back_to_back_prompts_stay_fast_inside_the_hot_window():
    # a chat: prompts every 12 s, i.e. inside the 30 s hot window => never slower than the fast interval
    _, lat = simulate(1.0, 5.0, 30.0, duration=600, job_at=[50 + 12 * i for i in range(40)], seed=5)
    assert max(lat[1:]) <= 1.0 + 1e-9


def test_config_env_and_validation(monkeypatch):
    env = {"ENIGMA_API_URL": "https://api.example", "ENIGMA_RUNTIME_TOKEN": TOKEN, "ENIGMA_CLAIM_POLL_IDLE_S": "5", "ENIGMA_CLAIM_POLL_S": "0.5", "ENIGMA_CLAIM_HOT_WINDOW_S": "45"}
    c = Config.from_env(env)
    assert (c.claim_poll_s, c.claim_poll_idle_s, c.claim_hot_window_s) == (0.5, 5.0, 45.0)
    assert Config.from_env({k: v for k, v in env.items() if "CLAIM" not in k}).claim_poll_idle_s == 5.0      # default (2026-11-22): conservative idle ceiling
    for bad in ({"ENIGMA_CLAIM_POLL_IDLE_S": "999"}, {"ENIGMA_CLAIM_POLL_S": "0.001"}, {"ENIGMA_CLAIM_POLL_S": "abc"}):
        with pytest.raises(ConfigError):
            Config.from_env({**env, **bad})
