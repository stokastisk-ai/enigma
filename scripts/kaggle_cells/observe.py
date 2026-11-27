# Survival observation (shared; generated from scripts/kaggle_cells/observe.py). PASSIVE by default: local, fsynced, printed MARK lines only (no
# network at all), with explicit sleep-begin / sleep-end markers, so the saved notebook output brackets a platform termination to within one tick.
# Remote diagnostic markers (OCI deployment) are OPT-IN at a few explicit offsets (bounded: at most 12 per window), each bracketed by local remote-submit / remote-ack markers.
# A missing cgroup OOM counter is reported as "unavailable", never as 0.
def oom_kill_count():
    ev = host()["cgroup_events"]
    return ev.get("oom_kill") if "oom_kill" in ev else None


def tick_schedule(seconds, every, dense_first_s=0.0, dense_every=2.0):
    """Monotonic offsets (s) at which a passive tick is recorded: every `dense_every` s for the first `dense_first_s` s, then every `every` s."""
    out, t = [], 0.0
    while True:
        step = dense_every if t < dense_first_s else every
        t = round(min(seconds, t + step), 3)
        out.append(t)
        if t >= seconds:
            return out


def observe(step, seconds, every=15.0, dense_first_s=0.0, dense_every=2.0, remote_at=(), sample=None, durable_begin=False):
    """Survival window. remote_at: offsets (s) where ONE bounded diagnostic marker is sent (active sampling); () = fully passive.
    durable_begin: the window starts only after the OCI deployment acknowledged an observation-start marker. PASS = full duration elapsed, no OOM kill observed,
    no failed sample, and every requested remote marker acknowledged."""
    remote_at = sorted(float(x) for x in remote_at)
    assert len(remote_at) <= 12, "remote markers per window are bounded"
    oom0 = oom_kill_count()
    if durable_begin:
        rcheck(step, step + "_obs_begin", stage=step, phase="before", note=f"window {seconds}s remote_at={remote_at}")
    start, problems, sent, n = time.monotonic(), [], [], 0
    mark(step + "_begin", seconds=seconds, every=every, dense_first_s=dense_first_s, dense_every=dense_every, remote_at=remote_at,
         oom_state="available" if oom0 is not None else "unavailable")
    for target in tick_schedule(seconds, every, dense_first_s, dense_every):
        n += 1
        mark(step + "_sleep_begin", i=n, until_s=target)
        time.sleep(max(0.0, target - (time.monotonic() - start)))
        el = round(time.monotonic() - start, 2)
        mark(step + "_sleep_end", i=n, elapsed_s=el)
        extra = sample() if sample else {}
        if extra.get("problem"):
            problems.append(extra["problem"])
        while remote_at and el + 1e-6 >= remote_at[0]:
            off = remote_at.pop(0)
            mark(step + "_remote_submit", offset_s=off)
            res = rmark(step + "_alive", stage=step, phase="observe", offset_s=off)
            mark(step + "_remote_result", offset_s=off, code=res.get("code"), acknowledged=bool(res))
            sent.append(bool(res))
            if not res and res.get("code") != "REMOTE_DISABLED":
                problems.append(f"remote marker at {off}s not acknowledged ({res.get('code')})")
        if extra:
            mark(step + "_sample", i=n, **extra)
    el, oom1 = time.monotonic() - start, oom_kill_count()
    delta = "unavailable" if oom0 is None or oom1 is None else oom1 - oom0
    ok = el >= seconds - 1.0 and delta in (0, "unavailable") and not problems
    mark(step + "_complete", observed_s=round(el, 2), ticks=n, remote_sent=len(sent), remote_acknowledged=sum(sent))
    verdict(step, "PASS" if ok else "FAIL", f"observed {el:.0f}/{seconds}s, oom_kill delta {delta}, remote {sum(sent)}/{len(sent)} acknowledged, "
            f"problems {problems[:3]}", observed_s=round(el, 1), oom_kill_delta=delta, ticks=n, remote_sent=len(sent), remote_acknowledged=sum(sent))
