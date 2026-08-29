#!/usr/bin/env python3
"""
calm_watcher.py — ntfy push the instant a Beefy CLM pool enters the calm zone.

Polls the strategy contract's isCalm() on Robinhood Chain every POLL_SECS.
On the transition blocked -> calm, fires an urgent ntfy push (with the
tick gap so you can judge how solid the window is), then respects a
cooldown so a whipsawing pool doesn't spam you.

Built to run as a GitHub Actions workflow_dispatch job: reads NTFY_TOPIC
from the environment and self-stops before the 6h job cap (with an
"ended" push so you know to re-trigger). Stdlib only. No dependencies.

Usage:
    NTFY_TOPIC=xxx python3 calm_watcher.py --test   # one test push, exit
    NTFY_TOPIC=xxx python3 calm_watcher.py          # run the watcher
"""

import json
import os
import sys
import time
import urllib.request

# ── config ──────────────────────────────────────────────────────────
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "CHANGE-ME")
MAX_HOURS  = float(os.environ.get("MAX_HOURS", "5.8"))  # self-stop before GH's 6h cap

POOL_NAME  = "GLD-USDG"
STRATEGY   = "0x8237F673A33eE64c25B3ec93c1582d589BFEEa38"  # uniswap-cow-robinhood-usdg-gld
VAULT_URL  = "https://app.beefy.com/vault/uniswap-cow-robinhood-usdg-gld"

RPC        = "https://rpc.mainnet.chain.robinhood.com"
NTFY_URL   = "https://ntfy.sh"
POLL_SECS  = 1.5   # seconds between checks
COOLDOWN   = 30    # min seconds between calm pushes (whipsaw guard)
ERR_LIMIT  = 20    # consecutive RPC failures before a "watcher degraded" push
# ────────────────────────────────────────────────────────────────────

SEL = {
    "isCalm":           "0x9bdde46b",
    "currentTick":      "0x065e5360",
    "twap":             "0x1208aa18",
    "maxTickDeviation": "0x696c58e5",
}

UA = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}


def eth_call(selector: str) -> str:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": STRATEGY, "data": selector}, "latest"],
    }).encode()
    req = urllib.request.Request(RPC, payload, UA)
    resp = json.load(urllib.request.urlopen(req, timeout=10))
    if "error" in resp:
        raise RuntimeError(resp["error"])
    return resp["result"]


def to_int(hexword: str) -> int:
    v = int(hexword, 16)
    return v - (1 << 256) if v >= (1 << 255) else v


def read_state():
    calm = int(eth_call(SEL["isCalm"]), 16) == 1
    tick = to_int(eth_call(SEL["currentTick"]))
    twap = to_int(eth_call(SEL["twap"]))
    dev  = to_int(eth_call(SEL["maxTickDeviation"]))
    return calm, abs(tick - twap), dev


def push(title: str, body: str, priority: str = "urgent", tags: str = "rotating_light"):
    req = urllib.request.Request(
        f"{NTFY_URL}/{NTFY_TOPIC}",
        data=body.encode(),
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": tags,
            "Click": VAULT_URL,
        },
    )
    urllib.request.urlopen(req, timeout=10)


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    if NTFY_TOPIC == "CHANGE-ME":
        sys.exit("set the NTFY_TOPIC environment variable first.")

    if "--test" in sys.argv:
        push(f"{POOL_NAME} watcher test", "if you can read this, pushes work.",
             priority="default", tags="white_check_mark")
        log("test push sent — check your phone.")
        return

    deadline = time.time() + MAX_HOURS * 3600
    push(f"{POOL_NAME} watcher running",
         f"auto-stops in ~{MAX_HOURS:g}h. you'll get an urgent push the moment it's calm.",
         priority="low", tags="eyes")
    log(f"watching {POOL_NAME} ({STRATEGY[:10]}…) every {POLL_SECS}s, for {MAX_HOURS:g}h")

    was_calm = None
    last_push = 0.0
    errors = 0
    degraded_notified = False

    while time.time() < deadline:
        try:
            calm, gap, dev = read_state()
            if errors >= ERR_LIMIT:
                log("rpc recovered.")
            errors = 0
            degraded_notified = False

            log(f"{'CALM  ' if calm else 'BLOCKED'}  gap {gap}/{dev} ticks")

            if calm and was_calm is False and time.time() - last_push > COOLDOWN:
                depth = dev - gap
                push(
                    f"🟢 {POOL_NAME} CALM — withdraw now",
                    f"gap {gap}/{dev} ticks ({depth} inside the band). "
                    f"tap to open the vault.",
                )
                last_push = time.time()
                log(">>> push sent <<<")

            was_calm = calm

        except KeyboardInterrupt:
            log("stopped.")
            return
        except Exception as e:
            errors += 1
            log(f"rpc error ({errors}/{ERR_LIMIT}): {e}")
            if errors == ERR_LIMIT and not degraded_notified:
                try:
                    push(f"{POOL_NAME} watcher degraded",
                         "rpc failing repeatedly — watcher may be blind. check the run logs.",
                         priority="high", tags="warning")
                    degraded_notified = True
                except Exception:
                    pass

        time.sleep(POLL_SECS)

    log("time limit reached, exiting.")
    push(f"{POOL_NAME} watcher ended",
         "hit the time limit. re-run the workflow if you still need eyes on it.",
         priority="high", tags="hourglass_flowing_sand")


if __name__ == "__main__":
    main()
