"""Scheduler control tools.

Manage the continuous `--update-all --every N` loop (the `scheduler` container)
through a shared control file on the ./data volume. The running scheduler re-reads
that file each cycle and during its sleep, so changes take effect within ~30s without
restarting the container.
"""
import time

from scheduler import control as sched_control
from mcp_server.proxy import api_get


def register(mcp):
    @mcp.tool()
    async def scheduler_status() -> dict:
        """Report the auto-update scheduler's state.

        The gap between cycles is randomised: a uniform random delay between
        gap_min_seconds (15s) and gap_max_seconds (the configured interval, capped at
        5 min). Returns whether it's enabled or paused, that gap window, the gap chosen
        for the current sleep, when the last cycle started/finished and whether it
        succeeded, seconds until the next cycle, and a liveness check (heartbeat age).
        When the heartbeat is stale the scheduler container is likely down or stuck.
        Also includes aggregate job stats.
        """
        ctrl = sched_control.read_control()
        status = sched_control.read_status()
        now = time.time()

        hb_epoch = status.get("heartbeat_epoch")
        heartbeat_age = int(now - hb_epoch) if hb_epoch else None
        interval = ctrl.get("interval_minutes") or status.get("interval_minutes")
        gap_max = sched_control.effective_max_gap_seconds(interval)
        # Liveness: stale if no heartbeat within 3 max-gaps plus a cycle's grace.
        stale_after = max(gap_max * 3, 600)
        alive = heartbeat_age is not None and heartbeat_age < stale_after

        next_epoch = status.get("next_run_epoch")
        seconds_until_next = int(next_epoch - now) if next_epoch else None

        out = {
            "enabled": ctrl.get("enabled", True),
            "running": alive,
            "interval_minutes": interval,
            "gap_min_seconds": sched_control.MIN_GAP_SECONDS,
            "gap_max_seconds": gap_max,
            "current_gap_seconds": status.get("next_sleep_seconds"),
            "heartbeat_age_seconds": heartbeat_age,
            "last_run_started": status.get("last_run_started"),
            "last_run_finished": status.get("last_run_finished"),
            "last_run_ok": status.get("last_run_ok"),
            "next_run": status.get("next_run_iso"),
            "seconds_until_next": max(seconds_until_next, 0) if seconds_until_next is not None else None,
        }
        if not alive:
            out["warning"] = ("No recent heartbeat — the scheduler container may be "
                              "down, paused, or starting up.")
        try:
            out["job_stats"] = await api_get("/jobs/stats")
        except Exception as e:
            out["job_stats_error"] = str(e)
        return out

    @mcp.tool()
    async def scheduler_set_interval(minutes: int) -> dict:
        """Set the UPPER BOUND of the randomised gap between update cycles.

        The actual delay before each cycle is random: uniform between 15s and this
        value, hard-capped at 5 minutes. So values above 5 are clamped to 5. Takes
        effect within ~5s (the running loop re-reads this during its sleep); no
        container restart. `minutes` must be >= 1.
        """
        if minutes < 1:
            raise ValueError("minutes must be >= 1")
        ctrl = sched_control.write_control(interval_minutes=minutes)
        effective_max = sched_control.effective_max_gap_seconds(ctrl["interval_minutes"])
        return {
            "status": "updated",
            "interval_minutes": ctrl["interval_minutes"],
            "effective_gap_seconds": f"{sched_control.MIN_GAP_SECONDS}..{effective_max:.0f}",
            "note": "Gap before each cycle is randomised within this window. Applies within ~5s.",
        }

    @mcp.tool()
    async def scheduler_pause() -> dict:
        """Pause the scheduler so it skips update cycles until resumed.

        An in-flight cycle finishes; the next one is skipped. The container keeps
        running (and keeps its heartbeat), so scheduler_status will show
        enabled=false, running=true.
        """
        sched_control.write_control(enabled=False)
        return {"status": "paused", "note": "Next cycle will be skipped. Call scheduler_resume to re-enable."}

    @mcp.tool()
    async def scheduler_resume() -> dict:
        """Resume the scheduler after a pause; update cycles start again."""
        sched_control.write_control(enabled=True)
        return {"status": "resumed", "note": "Updates resume on the next cycle (within ~30s)."}
