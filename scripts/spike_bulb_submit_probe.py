"""Q2 + B1 probe for bulb_tightening: per-seed reset assertion, submission verdict.

Split out of ``spike_bulb_tightening.py`` because neither question needs a grasp:

  Q2  does ``/reset`` with a seed != physics_seed raise?  In the full spike this
      is unanswerable, because the task raises the glass-fracture guard as the
      FIRST line of ``reset()`` (bulb_tightening.py:1441) while the per-seed
      assertion lives in ``_reset_actors()`` (:1457), reached only through
      ``super().reset()``.  Any episode that ends in a fracture masks Q2.  Here
      we ask it on a fresh process, before touching the bulb.

  B1  does ``/submit_episode`` produce a meaningful ``check_success()`` verdict?
      The endpoint's 60 s timeout is too short for the terminal review (120 sim
      steps, 180 when fractured, with a ``set_gripper`` each), so the first
      attempt answers 503 "Simulation busy".  That is recoverable: the review
      keeps running on the main thread, ``_finish_bulb_episode`` sets
      ``_accepted_result`` BEFORE the review and returns early on re-entry
      (:1750-1754), so a retry lands the verdict immediately.  This probe
      retries instead of giving up.

Usage:
    env -u ALL_PROXY -u all_proxy python scripts/spike_bulb_submit_probe.py
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

OUT_DIR = Path("outputs/spike_bulb_tightening")


class Probe:
    def __init__(self, base_url: str, verbose: bool = True):
        self.base_url = base_url.rstrip("/")
        self.verbose = verbose
        self.http = requests.Session()
        self.out: dict = {"base_url": self.base_url, "events": []}

    # -- plumbing ---------------------------------------------------------

    def say(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    @staticmethod
    def _detail(r: requests.Response) -> str:
        try:
            body = r.json()
        except ValueError:
            return r.text[:400]
        return str(body.get("detail", body))[:400]

    def _post(self, path: str, body: dict, timeout: float) -> requests.Response:
        return self.http.post(f"{self.base_url}{path}", json=body, timeout=timeout)

    def _get(self, path: str, timeout: float = 60.0) -> dict:
        r = self.http.get(f"{self.base_url}{path}", timeout=timeout)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} on {path}: {self._detail(r)}")
        return r.json()

    def _record(self, name: str, data: dict) -> None:
        self.out["events"].append({"t": time.time(), "name": name, "data": data})

    # -- Q2 ---------------------------------------------------------------

    def reset(self, seed: int, timeout: float = 900.0) -> dict:
        """POST /reset and return what happened, including the failure text."""
        t0 = time.time()
        r = self._post("/reset", {"seed": seed}, timeout=timeout)
        secs = round(time.time() - t0, 1)
        if r.status_code == 200:
            return {"seed": seed, "ok": True, "seconds": secs, "body": r.json()}
        return {"seed": seed, "ok": False, "seconds": secs,
                "status": r.status_code, "detail": self._detail(r)}

    def q2_per_seed_assertion(self) -> None:
        self.say("=== Q2: does /reset with a seed != physics_seed raise? ===")
        st = self._get("/force_state")
        phys = (st.get("params") or {}).get("physics_seed")
        self.say(f"  physics_seed (bound at init) = {phys}")

        first = self.reset(0)
        self.say(f"  reset(seed=0) -> ok={first['ok']} in {first['seconds']}s")
        if not first["ok"]:
            self.say(f"  UNEXPECTED: baseline reset failed: {first.get('detail')}")
            self._record("q2", {"baseline_failed": first})
            return

        second = self.reset(1)
        self.say(f"  reset(seed=1) -> ok={second['ok']} in {second['seconds']}s")
        if second["ok"]:
            self.say("  [Q2] NOT BLOCKED — reset accepted a foreign seed")
            verdict = "not_blocked"
        else:
            self.say(f"  [Q2] BLOCKED — HTTP {second.get('status')}: {second.get('detail')}")
            verdict = "blocked"
            # Distinguish the per-seed assertion from the fracture guard: the
            # guard's text is the only other way reset() can refuse, and if we
            # see it here the whole probe is invalid.
            if "fracture" in str(second.get("detail", "")).lower():
                verdict = "masked_by_fracture_guard"
                self.say("  !! masked by the fracture guard — this run is INVALID")

        # A refused reset must not poison the task: prove seed 0 still works.
        recovered = self.reset(0)
        self.say(f"  reset(seed=0) again -> ok={recovered['ok']} in {recovered['seconds']}s")
        self._record("q2", {"physics_seed": phys, "verdict": verdict,
                            "reset_seed_0": first, "reset_seed_1": second,
                            "recovered": recovered})

    # -- B1 ---------------------------------------------------------------

    def submit_with_retry(self, attempts: int = 30, pause: float = 30.0) -> dict:
        """POST /submit_episode, retrying through the 503 while the review runs."""
        tries = []
        for i in range(attempts):
            t0 = time.time()
            r = self._post("/submit_episode", {}, timeout=900.0)
            secs = round(time.time() - t0, 1)
            if r.status_code == 200:
                tries.append({"attempt": i, "seconds": secs, "status": 200})
                return {"ok": True, "attempts": tries, "verdict": r.json()}
            detail = self._detail(r)
            tries.append({"attempt": i, "seconds": secs,
                          "status": r.status_code, "detail": detail})
            self.say(f"    attempt {i}: HTTP {r.status_code} after {secs}s — retrying")
            if "Simulation busy" not in detail:
                return {"ok": False, "attempts": tries,
                        "verdict": {"error": f"HTTP {r.status_code}: {detail}"}}
            time.sleep(pause)
        return {"ok": False, "attempts": tries, "verdict": {"error": "retries exhausted"}}

    def b1_submission(self) -> None:
        self.say("=== B1: does /submit_episode return a meaningful verdict? ===")
        before = self._get("/force_state")
        self.say(f"  before: submission_requested={before.get('submission_requested')} "
                 f"failure={before.get('failure')} phase={before.get('phase')}")
        self.say("  submitting (retrying through the 60 s endpoint timeout) ...")
        res = self.submit_with_retry()
        self.say(f"  [B1] {'VERDICT OBTAINED' if res['ok'] else 'NO VERDICT'} "
                 f"after {len(res['attempts'])} attempt(s)")
        self.say(f"  /submit_episode -> {json.dumps(res['verdict'])[:700]}")
        after = self._get("/force_state")
        self.say(f"  after: submission_requested={after.get('submission_requested')} "
                 f"failure={after.get('failure')} phase={after.get('phase')} "
                 f"probe_complete={after.get('probe_complete')}")
        self._record("b1", {"ok": res["ok"], "attempts": res["attempts"],
                            "verdict": res["verdict"],
                            "before": {k: before.get(k) for k in
                                       ("submission_requested", "failure", "phase")},
                            "after": {k: after.get(k) for k in
                                      ("submission_requested", "failure", "phase",
                                       "probe_complete")}})

    # -- driver -----------------------------------------------------------

    def run(self) -> None:
        self.say(f"base_url {self.base_url}")
        self.say(f"  /health: {self._get('/health')}")
        self.q2_per_seed_assertion()
        self.b1_submission()
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / "spike_bulb_submit_probe.json"
        path.write_text(json.dumps(self.out, indent=2, default=str) + "\n")
        self.say(f"\nwrote {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8120")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    Probe(args.base_url, verbose=not args.quiet).run()


if __name__ == "__main__":
    main()
