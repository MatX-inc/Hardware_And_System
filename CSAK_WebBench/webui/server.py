"""CSAK Web Bench — Phase 1 (read-only dashboard).

Run from CSAK_EVB/webui/:
    .venv/bin/uvicorn server:app --port 8321
then open http://localhost:8321
"""
import glob
import json
import os
import subprocess
import sys
import threading

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import driver as drv
import parsers

HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="CSAK Web Bench")
driver = drv.AaplDriver()

VALID_OCTALS = {o["addr"] for die in driver.profile["dies"] for o in die["octals"]}


@app.get("/api/health")
def health():
    return {
        "board_reachable": driver.board_reachable(),
        "offline_demo": driver.offline(),
        "server": driver.server,
        "aapl": driver.aapl,
    }


@app.get("/api/profile")
def profile():
    return driver.profile


@app.get("/api/power")
def power():
    return parsers.parse_ps2_display(driver.ps2_display())


@app.get("/api/device")
def device():
    return parsers.parse_device_info(driver.device_info())


@app.get("/api/lanes")
def lanes(addr: str = Query(..., description="octal address, e.g. :9 or 1::8")):
    if addr not in VALID_OCTALS:
        return {"error": f"unknown octal '{addr}'", "valid": sorted(VALID_OCTALS)}
    detail = parsers.parse_display_lane(driver.display_lane(addr))
    locks = parsers.parse_pmd_lock(driver.pmd_lock(addr))
    for lane in detail["lanes"]:
        key = f"{addr}.{lane['lane']}"
        if key in locks:
            lane["pmd_lock"] = locks[key]
    detail["addr"] = addr
    return detail


@app.get("/api/locks")
def locks(addrs: str = Query(..., description="comma-separated octal addresses, e.g. :9,:f")):
    """PMD RX lock per lane for several octals in one request (one cheap
    ~0.1 s aapl read per octal). Lets the board map refresh every octal's
    lane strip after an action, not just the selected one — e.g. lane-init
    on :e also brings its partner :8 up, and :8 should turn green too."""
    result = {}
    for addr in [a.strip() for a in addrs.split(",") if a.strip()]:
        if addr not in VALID_OCTALS:
            result[addr] = {"error": "unknown octal"}
            continue
        locks = parsers.parse_pmd_lock(driver.pmd_lock(addr))
        result[addr] = {int(k.rsplit(".", 1)[1]): v for k, v in locks.items()
                        if k.startswith(addr + ".")}
    return result


@app.get("/api/ber")
def ber(addr: str = Query(...), dwell: int = Query(10, ge=1, le=600),
        lane: int | None = Query(None, ge=0, le=7)):
    if addr not in VALID_OCTALS:
        return {"error": f"unknown octal '{addr}'", "valid": sorted(VALID_OCTALS)}
    result = parsers.parse_ber(driver.ber(addr, dwell, lane))
    result["addr"] = addr
    return result


@app.get("/api/log")
def log():
    return {"entries": driver.log()}


@app.post("/api/log/clear")
def log_clear():
    return {"ok": True, "cleared": driver.clear_log()}


# ---------------------------------------------------------------------------
# Phase 2: control endpoints. All refuse in offline/demo mode (see driver).
# ---------------------------------------------------------------------------
def _ctl_result(pair):
    out, entry = pair
    if out is None:
        return {"ok": False, **entry}
    return {"ok": True, "output": out, "cmd": entry.get("cmd", "")}


@app.post("/api/power/on")
def power_on():
    return _ctl_result(driver.ps2_load())


@app.post("/api/power/off")
def power_off():
    return _ctl_result(driver.ps2_seq_off())


def _check_octal(addr):
    if addr not in VALID_OCTALS:
        return {"ok": False, "error": f"unknown octal '{addr}'"}
    return None


@app.post("/api/octal/fw-upload")
def octal_fw_upload(addr: str = Query(...)):
    return _check_octal(addr) or _ctl_result(driver.fw_upload(addr))


@app.post("/api/octal/core-init")
def octal_core_init(addr: str = Query(...)):
    return _check_octal(addr) or _ctl_result(driver.core_init(addr))


@app.get("/api/core")
def core_status(addr: str = Query(..., description="octal address, e.g. :9 or 1::8")):
    return parsers.parse_display_core(driver.display_core(addr))


@app.post("/api/octal/lane-init")
def octal_lane_init(addr: str = Query(...), lane: int | None = Query(None, ge=0, le=7)):
    """Lane-init; when the core PLL is not yet initialised (fresh Upload FW /
    POR reset) Core-init is run first automatically, so the user never has to
    remember the ordering. No firmware at all is still refused."""
    bad = _check_octal(addr)
    if bad:
        return bad
    auto_core = None
    if not driver.offline():
        core = parsers.parse_display_core(driver.display_core(addr))
        if core["state"] == "no_fw":
            return {"ok": False, "error":
                    f"no firmware running on {addr} ({core['error'] or 'no CORE status'}) — "
                    "run 1 · Upload FW first"}
        if core["state"] == "needs_core_init":
            out, entry = driver.core_init(addr)
            if out is None:
                return {"ok": False, **entry}
            core = parsers.parse_display_core(driver.display_core(addr))
            if core["state"] != "ready":
                return {"ok": False, "error":
                        f"auto Core-init on {addr} did not bring the PLL up (PLL_LOCK="
                        f"{int(core['pll_lock'])}, PLL_PWDN={core['pll_pwdn']}, div={core['pll_div']})",
                        "output": out}
            auto_core = f"core-init {addr} run automatically (PLL now locked, div {core['pll_div']})\n"
    res = _ctl_result(driver.lane_init(addr, lane))
    if auto_core and res.get("ok"):
        res["output"] = auto_core + res.get("output", "")
        res["auto_core_init"] = True
    return res


@app.post("/api/lane/tx-output")
def lane_tx_output(addr: str = Query(...), lane: int = Query(..., ge=0, le=7),
                   enable: int = Query(..., ge=0, le=1)):
    return _check_octal(addr) or _ctl_result(driver.tx_output(addr, lane, bool(enable)))


@app.post("/api/lane/dp-reset")
def lane_dp_reset(addr: str = Query(...), lane: int = Query(..., ge=0, le=7),
                  assert_reset: int = Query(..., alias="assert", ge=0, le=1)):
    """assert=1 stops the lane (datapath held in reset); assert=0 restarts it."""
    return _check_octal(addr) or _ctl_result(driver.lane_dp_reset(addr, lane, bool(assert_reset)))


@app.post("/api/octal/por-reset")
def octal_por_reset(addr: str = Query(...)):
    """Pulse POR on one octal: firmware and lane state are wiped for that octal."""
    return _check_octal(addr) or _ctl_result(driver.octal_por_reset(addr))


@app.post("/api/arp-fix")
def arp_fix():
    return driver.arp_fix()


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"),
                        headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

# ---------------------------------------------------------------------------
# Crosstalk A/B test (crosstalk.py) — launched as a subprocess so the CLI and
# the UI share one code path; the UI follows the run's status.json.
# ---------------------------------------------------------------------------
RESULTS_ROOT = os.path.normpath(os.path.join(HERE, "..", "crosstalk_results"))
os.makedirs(RESULTS_ROOT, exist_ok=True)
app.mount("/results", StaticFiles(directory=RESULTS_ROOT), name="results")
_XT = {"proc": None, "dir": None, "cfg": None, "log": None}
_XT_LOCK = threading.Lock()


def _xt_status_from_dir(d):
    try:
        with open(os.path.join(d, "status.json")) as f:
            return json.load(f)
    except Exception:
        return None


def _xt_newest_dir():
    dirs = sorted(glob.glob(os.path.join(RESULTS_ROOT, "*_L[0-9]")), key=os.path.getmtime)
    return dirs[-1] if dirs else None


@app.post("/api/crosstalk/start")
def crosstalk_start(addr: str = Query(...), lane: int = Query(0, ge=0, le=7),
                    rounds: int = Query(3, ge=1, le=50), dwell: int = Query(10, ge=1, le=600),
                    screenshots: int = Query(1, ge=0, le=1)):
    if addr not in VALID_OCTALS:
        return {"ok": False, "error": f"unknown octal '{addr}'"}
    if driver.offline():
        return {"ok": False, "error": "board unreachable — demo mode is read-only"}
    with _XT_LOCK:
        p = _XT["proc"]
        if p is not None and p.poll() is None:
            return {"ok": False, "error": "a crosstalk run is already in progress"}
        before = set(glob.glob(os.path.join(RESULTS_ROOT, "*_L[0-9]")))
        argv = [sys.executable, os.path.join(HERE, "crosstalk.py"), "--octal", addr, "--lane", str(lane),
                "--rounds", str(rounds), "--dwell", str(dwell), "--url", "http://127.0.0.1:8321"]
        if not screenshots:
            argv.append("--no-screenshots")
        logf = open(os.path.join(RESULTS_ROOT, "last_run.log"), "w")
        proc = subprocess.Popen(argv, cwd=HERE, stdout=logf, stderr=subprocess.STDOUT)
        _XT.update(proc=proc, dir=None, cfg={"addr": addr, "lane": lane, "rounds": rounds, "dwell": dwell}, log=logf)
        # the script creates its result dir within a second; find it
        import time as _t
        for _ in range(40):
            _t.sleep(0.25)
            new = set(glob.glob(os.path.join(RESULTS_ROOT, "*_L[0-9]"))) - before
            if new:
                _XT["dir"] = sorted(new)[-1]
                break
        driver._record(argv, "(crosstalk run started)", 0.0, "local", "action")
        return {"ok": True, "pid": proc.pid, "result_dir": os.path.basename(_XT["dir"] or "")}


@app.get("/api/crosstalk/status")
def crosstalk_status():
    d = _XT["dir"] or _xt_newest_dir()
    if not d:
        return {"state": "idle"}
    st = _xt_status_from_dir(d) or {"state": "unknown"}
    p = _XT["proc"]
    running = p is not None and p.poll() is None
    if not running and st.get("state") == "running":
        st["state"] = "error"
        st["message"] = st.get("message", "") + " (process exited unexpectedly — see last_run.log)"
    st["running"] = running
    st["result_dir"] = os.path.basename(d)
    st["url_base"] = f"/results/{os.path.basename(d)}/"
    return st


@app.get("/api/crosstalk/list")
def crosstalk_list():
    out = []
    for d in sorted(glob.glob(os.path.join(RESULTS_ROOT, "*_L[0-9]")), reverse=True):
        st = _xt_status_from_dir(d)
        if not st:
            continue
        cfg = st.get("cfg", {})
        out.append({"dir": os.path.basename(d), "state": st.get("state"), "started": st.get("started"),
                    "octal": cfg.get("octal"), "partner": cfg.get("partner"), "lane": cfg.get("lane"),
                    "rounds": cfg.get("rounds"), "dwell": cfg.get("dwell"),
                    "has_pdf": os.path.exists(os.path.join(d, "report.pdf")),
                    "summary": st.get("summary")})
    return {"runs": out[:30]}
