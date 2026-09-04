#!/usr/bin/env python3
"""Crosstalk A/B BER test for one victim lane on a CSAK link.

Phase A (isolated): every lane except the victim is parked on BOTH octals
(serdes -lane-dp-reset 1 = RX+TX datapath held in reset, so aggressor
transmitters go quiet and their receivers stop). N timed BER rounds of T s
are run on the victim lane, measured at both ends of the link.

Phase B (all-on): all 8 lanes are released on both octals and re-lock.
The same N x T s BER rounds are run; the victim lane's error counts are
extracted, and the other lanes' counts are kept for context.

Everything goes through the CSAK Web Bench HTTP API (default
http://127.0.0.1:8321) so hardware access is serialized with the dashboard
(the AACS server is single-client). Results land in
CSAK_EVB/crosstalk_results/<timestamp>_<octal>_L<lane>/ as results.json,
rounds.csv, report.pdf and (if Chrome is available) two UI screenshots.

CLI:
    .venv/bin/python crosstalk.py --octal :8 --lane 0 --rounds 3 --dwell 10
The web UI's "Crosstalk" tab launches exactly this script as a subprocess and
follows its status.json.
"""
import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_ROOT = os.path.normpath(os.path.join(HERE, "..", "crosstalk_results"))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
class Api:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def get(self, path, timeout=120, **params):
        r = requests.get(self.base + path, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def post(self, path, timeout=180, **params):
        r = requests.post(self.base + path, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()


class Progress:
    """Writes status.json in the result dir after every step; the web UI polls it."""

    def __init__(self, result_dir, cfg):
        self.path = os.path.join(result_dir, "status.json")
        self.state = {
            "state": "running", "phase": "preflight", "round": 0, "rounds": cfg["rounds"],
            "message": "starting", "pct": 0, "started": dt.datetime.now().isoformat(timespec="seconds"),
            "updated": None, "result_dir": result_dir, "cfg": cfg, "rounds_done": [], "files": {},
            # live lane plan for the dashboard overlay: {octal: {lane: parked|victim|running}}
            "lane_states": {}, "measuring": None,
        }
        self.write()

    def set_lane(self, octal, lane, state):
        self.state["lane_states"].setdefault(octal, {})[str(lane)] = state
        self.write()

    def update(self, **kw):
        self.state.update(kw)
        self.write()
        msg = f"[{self.state['phase']}] {self.state['message']}"
        print(msg, flush=True)

    def write(self):
        self.state["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# Board operations (via the API)
# ---------------------------------------------------------------------------
def lane_snapshot(api, octal):
    d = api.get("/api/lanes", addr=octal)
    if d.get("error"):
        raise RuntimeError(f"/api/lanes {octal}: {d['error']}")
    out = {}
    for l in d["lanes"]:
        out[l["lane"]] = {
            "locked": bool(l.get("pmd_lock", l.get("locked"))),
            "stopped": bool(l.get("stopped")),
            "tx_output": l.get("tx_output"),
            "snr_db": l.get("snr_db"),
            "eye": l.get("eye"),
            "ber_display": l.get("ber"),
            "signal_detect": l.get("signal_detect"),
        }
    return {"ucode": d.get("ucode"), "api": d.get("api"), "lanes": out}


def set_dp_reset(api, octal, lanes, assert_reset, prog=None, victim=None):
    for ln in lanes:
        r = api.post("/api/lane/dp-reset", addr=octal, lane=ln, **{"assert": 1 if assert_reset else 0})
        if not r.get("ok"):
            raise RuntimeError(f"dp-reset {octal}.{ln} assert={assert_reset}: {r.get('error')}")
        if prog:
            prog.set_lane(octal, ln, "parked" if assert_reset else ("victim" if ln == victim else "running"))
            prog.update(message=f"{'stop' if assert_reset else 'start'} {octal}.{ln}")


def wait_locks(api, want, timeout_s, prog=None, label=""):
    """want = {octal: [lanes that must be locked]}. Returns (ok, last_locks)."""
    t0 = time.time()
    last = {}
    while True:
        d = api.get("/api/locks", addrs=",".join(want))
        last = d
        ok = all(all(d.get(o, {}).get(str(ln)) is True for ln in lanes) for o, lanes in want.items())
        if ok:
            return True, last
        if time.time() - t0 > timeout_s:
            return False, last
        if prog:
            prog.update(message=f"waiting for lock {label} ({int(time.time() - t0)} s)")
        time.sleep(2)


def ber_round(api, octal, lane, dwell, single):
    params = {"addr": octal, "dwell": dwell}
    if single:
        params["lane"] = lane
    d = api.get("/api/ber", timeout=dwell + 120, **params)
    if d.get("error"):
        raise RuntimeError(f"/api/ber {octal}: {d['error']}")
    victim = next((r for r in d["lanes"] if r["addr"] == f"{octal}.{lane}"), None)
    if victim is None:
        raise RuntimeError(f"BER output for {octal} has no row for lane {lane} (lane not running?)")
    return victim, d["lanes"]


def screenshot(url, out_png, width=1500, height=1000, budget_ms=12000):
    if not os.path.exists(CHROME):
        return False
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           f"--window-size={width},{height}", f"--virtual-time-budget={budget_ms}",
           f"--screenshot={out_png}", url]
    try:
        subprocess.run(cmd, capture_output=True, timeout=90)
    except Exception:
        return False
    return os.path.exists(out_png) and os.path.getsize(out_png) > 1000


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------
def run(cfg):
    api = Api(cfg["url"])
    A, L, N, T = cfg["octal"], cfg["lane"], cfg["rounds"], cfg["dwell"]
    profile = api.get("/api/profile")
    octals = {o["addr"]: o for die in profile["dies"] for o in die["octals"]}
    if A not in octals:
        sys.exit(f"unknown octal {A}")
    partner = cfg.get("partner") or octals[A].get("partner")
    B = None if partner in (None, "self") else partner
    sides = [A] + ([B] if B else [])
    cfg["partner"] = B or "self"
    cfg["line_rate_gbps"] = profile.get("line_rate_gbps")
    cfg["channel"] = octals[A].get("channel")
    cfg["board"] = profile.get("board")

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    result_dir = os.path.join(RESULTS_ROOT, f"{stamp}_{A.replace(':', '_')}_L{L}")
    os.makedirs(result_dir, exist_ok=True)
    prog = Progress(result_dir, cfg)

    res = {"cfg": cfg, "result_dir": result_dir, "started": prog.state["started"],
           "preflight": {}, "phases": {}, "restore": {}, "files": {}}

    try:
        # ---- preflight ----------------------------------------------------
        h = api.get("/api/health")
        if h.get("offline_demo"):
            raise RuntimeError("board unreachable (dashboard in OFFLINE DEMO) — cannot run")
        for o in sides:
            core = api.get("/api/core", addr=o)
            if core.get("state") != "ready":
                raise RuntimeError(f"{o}: core state {core.get('state')} — upload firmware / core-init first")
        initial = {o: lane_snapshot(api, o) for o in sides}
        res["preflight"] = {"initial": initial}
        for o in sides:
            for ln, st in initial[o]["lanes"].items():
                prog.state["lane_states"].setdefault(o, {})[str(ln)] = "victim" if int(ln) == L else ("parked" if st["stopped"] else "running")
        prog.update(message=f"victim {A}.{L}" + (f" <-> {B}.{L}" if B else " (loopback)") + " — preflight ok", pct=3)
        others = [ln for ln in range(8) if ln != L]

        # ---- Phase A: isolated --------------------------------------------
        prog.update(phase="isolated", message="parking the other 7 lanes on both sides", pct=5)
        for o in sides:
            set_dp_reset(api, o, others, True, prog, victim=L)
            # make sure the victim itself is running
            set_dp_reset(api, o, [L], False, prog, victim=L)
        ok, locks = wait_locks(api, {o: [L] for o in sides}, cfg["lock_timeout"], prog, "victim")
        snapA = {o: lane_snapshot(api, o) for o in sides}
        pA = {"lock_ok": ok, "locks": locks, "snapshot": snapA, "rounds": [], "screenshot": None}
        res["phases"]["isolated"] = pA
        if not ok:
            raise RuntimeError(f"victim lane did not lock in isolated mode: {locks}")
        if cfg["screenshots"]:
            png = os.path.join(result_dir, "ui_isolated.png")
            prog.update(message="screenshot: isolated lane state", pct=8)
            if screenshot(f"{cfg['url']}/?octal={A}&pane=lanes", png):
                pA["screenshot"] = png
                res["files"]["ui_isolated"] = png
        for r in range(1, N + 1):
            prog.update(round=r, message=f"round {r}/{N}: BER {T} s on {A}.{L} (isolated)", pct=10 + 40 * (r - 1) / N)
            row = {"round": r}
            for o in sides:
                prog.update(measuring=f"{o}.{L}")
                v, _all = ber_round(api, o, L, T, single=True)
                row[o] = v
            prog.state["measuring"] = None
            pA["rounds"].append(row)
            prog.state["rounds_done"].append({"phase": "isolated", **{k: (v if k == "round" else v["errors"]) for k, v in row.items()}})
            prog.write()

        # ---- Phase B: all lanes on ---------------------------------------
        prog.update(phase="all-on", round=0, message="releasing all 8 lanes on both sides", pct=52)
        for o in sides:
            set_dp_reset(api, o, list(range(8)), False, prog, victim=L)
        ok, locks = wait_locks(api, {o: list(range(8)) for o in sides}, cfg["lock_timeout"], prog, "all 8 lanes")
        snapB = {o: lane_snapshot(api, o) for o in sides}
        pB = {"lock_ok": ok, "locks": locks, "snapshot": snapB, "rounds": [], "screenshot": None}
        res["phases"]["all_on"] = pB
        if not ok:
            prog.update(message=f"warning: not every lane locked in all-on mode: {locks}")
        if cfg["screenshots"]:
            png = os.path.join(result_dir, "ui_all_on.png")
            prog.update(message="screenshot: all-on lane state", pct=55)
            if screenshot(f"{cfg['url']}/?octal={A}&pane=lanes", png):
                pB["screenshot"] = png
                res["files"]["ui_all_on"] = png
        for r in range(1, N + 1):
            prog.update(round=r, message=f"round {r}/{N}: BER {T} s on {A}.* (all lanes on)", pct=58 + 38 * (r - 1) / N)
            row = {"round": r}
            for o in sides:
                prog.update(measuring=f"{o}.*")
                v, all_rows = ber_round(api, o, L, T, single=False)
                row[o] = v
                row[o + "_all"] = all_rows
            prog.state["measuring"] = None
            pB["rounds"].append(row)
            prog.state["rounds_done"].append({"phase": "all_on", **{k: (v if k == "round" else v["errors"]) for k, v in row.items() if not k.endswith("_all")}})
            prog.write()

        # ---- restore lanes that were stopped before the test --------------
        prog.update(phase="restore", message="restoring lanes that were stopped before the test", pct=97)
        for o in sides:
            # lanes parked before the test are parked again — except the victim,
            # which the user explicitly chose to exercise and expects to find running
            was_stopped = [ln for ln, st in initial[o]["lanes"].items() if st["stopped"] and int(ln) != L]
            if was_stopped:
                set_dp_reset(api, o, was_stopped, True, prog, victim=L)
            res["restore"][o] = {"re_stopped": was_stopped}

        res["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        res["summary"] = summarize(res)
        write_outputs(res, result_dir)
        prog.update(phase="done", state="done", message="report written", pct=100,
                    files={k: os.path.basename(v) for k, v in res["files"].items()}, summary=res["summary"])
        return res
    except Exception as e:  # noqa: BLE001 - always record the failure for the UI
        res["error"] = str(e)
        res["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        try:
            with open(os.path.join(result_dir, "results.json"), "w") as f:
                json.dump(res, f, indent=1, default=str)
        except Exception:
            pass
        prog.update(state="error", message=f"FAILED: {e}")
        raise


# ---------------------------------------------------------------------------
# Summary, CSV, PDF
# ---------------------------------------------------------------------------
def summarize(res):
    cfg = res["cfg"]
    A, L = cfg["octal"], cfg["lane"]
    B = None if cfg["partner"] == "self" else cfg["partner"]
    sides = [A] + ([B] if B else [])
    out = {"sides": sides, "victim": f"{A}.{L}", "per_side": {}}
    for o in sides:
        s = {}
        for name, key in (("isolated", "isolated"), ("all_on", "all_on")):
            rounds = res["phases"].get(key, {}).get("rounds", [])
            errs = [r[o]["errors"] for r in rounds if o in r]
            bits = [r[o]["bits"] for r in rounds if o in r]
            tot_e, tot_b = sum(errs), sum(bits)
            snap = res["phases"].get(key, {}).get("snapshot", {}).get(o, {}).get("lanes", {}).get(L) \
                or res["phases"].get(key, {}).get("snapshot", {}).get(o, {}).get("lanes", {}).get(str(L), {})
            s[name] = {
                "rounds": len(errs), "errors": errs, "total_errors": tot_e, "total_bits": tot_b,
                "mean_ber": (tot_e / tot_b) if tot_b else None,
                "ber_bound": (1.0 / tot_b) if (tot_b and tot_e == 0) else None,
                "snr_db": snap.get("snr_db") if snap else None,
                "eye": snap.get("eye") if snap else None,
            }
        iso, alo = s["isolated"], s["all_on"]
        if max(alo["total_errors"], iso["total_errors"]) < 10 and not (iso["total_errors"] == 0 and alo["total_errors"] >= 5):
            verdict = (f"too few errors to judge (isolated {iso['total_errors']}, all-on {alo['total_errors']} in "
                       f"{iso['total_bits']:.1e} bits) — increase dwell or rounds")
        elif alo["total_errors"] == 0 and iso["total_errors"] == 0:
            verdict = "no errors in either mode — no measurable crosstalk impact at this dwell"
        elif iso["total_errors"] == 0:
            verdict = f"{alo['total_errors']} errors appear only with all lanes on — crosstalk-induced degradation"
        else:
            ratio = alo["total_errors"] / iso["total_errors"]
            if ratio > 2:
                verdict = f"errors x{ratio:.1f} with all lanes on — significant crosstalk impact"
            elif ratio > 1.2:
                verdict = f"errors x{ratio:.1f} with all lanes on — mild crosstalk impact"
            elif ratio < 0.8:
                verdict = f"fewer errors with all lanes on (x{ratio:.2f}) — no crosstalk penalty; isolated result likely limited by statistics"
            else:
                verdict = f"errors x{ratio:.2f} — within round-to-round noise, no clear crosstalk impact"
        s["verdict"] = verdict
        if iso["snr_db"] is not None and alo["snr_db"] is not None:
            s["snr_delta_db"] = round(alo["snr_db"] - iso["snr_db"], 2)
        out["per_side"][o] = s
    return out


def write_outputs(res, result_dir):
    cfg = res["cfg"]
    sides = res["summary"]["sides"]
    # JSON
    p = os.path.join(result_dir, "results.json")
    with open(p, "w") as f:
        json.dump(res, f, indent=1, default=str)
    res["files"]["json"] = p
    # CSV
    p = os.path.join(result_dir, "rounds.csv")
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        hdr = ["phase", "round", "dwell_s"]
        for o in sides:
            hdr += [f"{o} errors", f"{o} bits", f"{o} BER", f"{o} BER is bound"]
        w.writerow(hdr)
        for key in ("isolated", "all_on"):
            for r in res["phases"].get(key, {}).get("rounds", []):
                row = [key, r["round"], cfg["dwell"]]
                for o in sides:
                    v = r[o]
                    row += [v["errors"], v["bits"], f"{v['ber']:.3e}", int(v["ber_is_bound"])]
                w.writerow(row)
    res["files"]["csv"] = p
    # PDF
    p = os.path.join(result_dir, "report.pdf")
    build_pdf(res, p)
    res["files"]["pdf"] = p
    # re-write JSON with file list
    with open(res["files"]["json"], "w") as f:
        json.dump(res, f, indent=1, default=str)


def _fmt_ber(v, bound=False):
    if v is None:
        return "—"
    return ("<" if bound else "") + f"{v:.2e}"


def build_pdf(res, path):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch, mm
    from reportlab.platypus import (HRFlowable, Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    cfg, summ = res["cfg"], res["summary"]
    A, L, B = cfg["octal"], cfg["lane"], (None if cfg["partner"] == "self" else cfg["partner"])
    sides = summ["sides"]
    NAVY = colors.HexColor("#2C3E50")
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9.5, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10.5, textColor=colors.HexColor("#555555"))
    cell = ParagraphStyle("cell", parent=body, fontSize=8.5, leading=11)
    mono = ParagraphStyle("mono", parent=body, fontName="Courier", fontSize=8, leading=10)
    h1, h2 = styles["Heading1"], styles["Heading2"]

    def tbl(data, col_widths=None, align_right_from=1):
        t = Table(data, colWidths=col_widths, hAlign="LEFT", repeatRows=1)
        st = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        if align_right_from is not None:
            st.append(("ALIGN", (align_right_from, 1), (-1, -1), "RIGHT"))
        t.setStyle(TableStyle(st))
        return t

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.grey)
        canvas.drawString(doc.leftMargin, 12 * mm, f"CSAK crosstalk test · {A}.{L} · {res['started']}")
        canvas.drawRightString(A4[0] - doc.rightMargin, 12 * mm, f"page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.9 * inch,
                            title=f"CSAK Crosstalk Test — {A}.{L}", author="CSAK Web Bench")
    S = []
    S.append(Paragraph("CSAK Crosstalk Test Report", styles["Title"]))
    link = f"{A}.{L} ↔ {B}.{L}" if B else f"{A}.{L} (loopback)"
    S.append(Paragraph(f"Victim lane <b>{link}</b> · single-lane (isolated) vs all-8-lanes-on BER comparison", body))
    S.append(Paragraph(f"Run {res['started']} → {res.get('finished', '')} · {cfg.get('board', '')}", small))
    S.append(Spacer(1, 6))
    S.append(HRFlowable(width="100%", thickness=0.8, color=NAVY))
    S.append(Spacer(1, 8))

    # ---- 1. Setup ----------------------------------------------------------
    S.append(Paragraph("1. Test setup", h2))
    init = res["preflight"].get("initial", {})
    fw = ", ".join(f"{o}: {init.get(o, {}).get('ucode', '?')}" for o in sides)
    apiv = init.get(A, {}).get("api", "?")
    setup = [["Parameter", "Value"],
             ["Victim octal / lane", f"{A} lane {L}"],
             ["Partner octal", B or "self (loopback channel)"],
             ["Channel", cfg.get("channel") or "see board profile"],
             ["Line rate", f"{cfg.get('line_rate_gbps', '?')} Gb/s PAM4 (PRBS31, link-trained)"],
             ["Firmware / API", f"{fw} · API {apiv}"],
             ["BER dwell per round", f"{cfg['dwell']} s"],
             ["Rounds per phase", str(cfg["rounds"])],
             ["Isolation method", "serdes -lane-dp-reset 1 on the other 7 lanes of both octals (RX+TX datapath held in reset: aggressor TX quiet, RX parked)"],
             ["All-on method", "serdes -lane-dp-reset 0 on all 8 lanes of both octals, wait for PMD lock on all 16 lanes"],
             ["Measurement", "serdes -ber -error-reset -ber-dwell T on the victim lane at each end (isolated: single-lane address; all-on: full octal, victim row extracted)"],
             ["Tooling", "CSAK Web Bench API (aapl 4.1.6 over AACS) · crosstalk.py"]]
    S.append(tbl([setup[0]] + [[Paragraph(str(c), cell) for c in r] for r in setup[1:]], [1.9 * inch, 4.9 * inch], align_right_from=None))
    S.append(Spacer(1, 10))

    # ---- 2. Method ---------------------------------------------------------
    S.append(Paragraph("2. Test plan / method", h2))
    steps = [
        "Preflight: confirm the dashboard is LIVE, both octals report core state <i>ready</i> (PLL locked), and record every lane's stopped/TX/lock state so it can be restored afterwards.",
        "Phase A — isolated: park lanes 0–7 except the victim on both octals (datapath reset), make sure the victim lane itself is released, and wait for PMD lock on the victim at both ends. Capture a lane-state snapshot (SNR, eye) and a dashboard screenshot.",
        f"Run {cfg['rounds']} BER rounds of {cfg['dwell']} s each on the victim lane at each end; the error counter is reset at the start of every round.",
        "Phase B — all-on: release all 8 lanes on both octals, wait for all 16 lanes to lock, capture snapshot + screenshot.",
        f"Run the same {cfg['rounds']} × {cfg['dwell']} s BER rounds; the victim lane's row is extracted from the full-octal BER result, the other lanes' counts are kept in results.json.",
        "Restore: any lane (other than the victim) that was stopped before the test is stopped again; the victim and all other lanes are left running.",
        "Compare: per-round error counts, totals, mean BER, SNR and eye margins between the two phases; the verdict is a plain ratio of all-on to isolated errors.",
    ]
    for i, s in enumerate(steps, 1):
        S.append(Paragraph(f"{i}. {s}", body))
    S.append(Spacer(1, 10))

    # ---- 3. Per-round results ----------------------------------------------
    S.append(Paragraph("3. Per-round results (victim lane)", h2))
    hdr = ["Round"]
    for o in sides:
        hdr += [f"Isolated\n{o}.{L} errors", f"Isolated\n{o}.{L} BER", f"All-on\n{o}.{L} errors", f"All-on\n{o}.{L} BER"]
    rows = [hdr]
    pa = res["phases"].get("isolated", {}).get("rounds", [])
    pb = res["phases"].get("all_on", {}).get("rounds", [])
    for i in range(max(len(pa), len(pb))):
        r = [str(i + 1)]
        for o in sides:
            a = pa[i][o] if i < len(pa) else None
            b = pb[i][o] if i < len(pb) else None
            r += [str(a["errors"]) if a else "—", _fmt_ber(a["ber"], a["ber_is_bound"]) if a else "—",
                  str(b["errors"]) if b else "—", _fmt_ber(b["ber"], b["ber_is_bound"]) if b else "—"]
        rows.append(r)
    n = len(hdr)
    S.append(tbl(rows, [0.6 * inch] + [(6.8 * inch - 0.6 * inch) / (n - 1)] * (n - 1)))
    S.append(Paragraph("BER shown with '&lt;' is an upper bound (zero errors in the dwell). bits = line rate × dwell.", small))
    S.append(Spacer(1, 10))

    # ---- 4. Summary --------------------------------------------------------
    S.append(Paragraph("4. Summary and comparison", h2))
    srows = [["Side", "Mode", "Rounds", "Total errors", "Total bits", "Mean BER", "SNR (dB)", "Eye U/M/L"]]
    for o in sides:
        ps = summ["per_side"][o]
        for name, lab in (("isolated", "isolated (1 lane)"), ("all_on", "all 8 lanes on")):
            m = ps[name]
            ber = _fmt_ber(m["mean_ber"]) if m["total_errors"] else _fmt_ber(m["ber_bound"], True)
            srows.append([f"{o}.{L}", lab, str(m["rounds"]), str(m["total_errors"]), f"{m['total_bits']:.3e}",
                          ber, f"{m['snr_db']:.2f}" if m["snr_db"] is not None else "—",
                          "/".join(map(str, m["eye"])) if m["eye"] else "—"])
    S.append(tbl(srows, [0.55 * inch, 1.15 * inch, 0.55 * inch, 0.85 * inch, 0.95 * inch, 0.85 * inch, 0.7 * inch, 0.85 * inch], align_right_from=2))
    S.append(Spacer(1, 6))
    for o in sides:
        ps = summ["per_side"][o]
        extra = f" SNR change {ps['snr_delta_db']:+.2f} dB." if "snr_delta_db" in ps else ""
        S.append(Paragraph(f"<b>{o}.{L}:</b> {ps['verdict']}.{extra}", body))
    S.append(Spacer(1, 10))

    # ---- 5. Screenshots ----------------------------------------------------
    shots = [(res["phases"].get("isolated", {}).get("screenshot"), "Isolated: only the victim lane running"),
             (res["phases"].get("all_on", {}).get("screenshot"), "All-on: all 8 lanes running")]
    if any(s[0] for s in shots):
        S.append(PageBreak())
        S.append(Paragraph("5. Dashboard snapshots", h2))
        for png, cap in shots:
            if png and os.path.exists(png):
                img = Image(png)
                w = 6.8 * inch
                img.drawHeight = w * img.imageHeight / img.imageWidth
                img.drawWidth = w
                S.append(img)
                S.append(Paragraph(cap, small))
                S.append(Spacer(1, 10))

    # ---- 6. Lane snapshots --------------------------------------------------
    S.append(Paragraph("6. Lane state snapshots (all lanes)", h2))
    for key, lab in (("isolated", "Isolated"), ("all_on", "All-on")):
        snap = res["phases"].get(key, {}).get("snapshot", {})
        for o in sides:
            lanes = snap.get(o, {}).get("lanes", {})
            if not lanes:
                continue
            rows = [["Lane", "State", "Lock", "SNR (dB)", "Eye U/M/L", "TX out"]]
            for ln in sorted(lanes, key=lambda x: int(x)):
                st = lanes[ln]
                rows.append([f"{o}.{ln}", "stopped" if st["stopped"] else "running", "1" if st["locked"] else "0",
                             f"{st['snr_db']:.2f}" if st["snr_db"] is not None else "—",
                             "/".join(map(str, st["eye"])) if st["eye"] else "—",
                             "on" if st["tx_output"] else ("off" if st["tx_output"] is False else "—")])
            S.append(Paragraph(f"{lab} — {o}", ParagraphStyle("h3", parent=body, fontName="Helvetica-Bold")))
            S.append(tbl(rows, [0.7 * inch, 0.9 * inch, 0.5 * inch, 0.8 * inch, 0.9 * inch, 0.6 * inch]))
            S.append(Spacer(1, 6))

    # ---- 7. Notes ----------------------------------------------------------
    S.append(Paragraph("7. Notes and limitations", h2))
    notes = [
        f"Statistics: at {cfg.get('line_rate_gbps', 212.5)} Gb/s a {cfg['dwell']} s dwell is ≈{cfg.get('line_rate_gbps', 212.5) * 1e9 * cfg['dwell']:.1e} bits per round; a zero-error round only bounds the BER, so longer dwells or more rounds are needed to resolve BER differences below that bound.",
        "Isolation uses the lane datapath reset, which silences the aggressor transmitters and parks their receivers. It does not power the lanes down, so supply-noise coupling from powered-but-idle lanes is still present; only data-dependent crosstalk is removed.",
        "Pre-FEC BER is reported (raw PRBS checker). Post-FEC performance is not part of this test.",
        "Link training was left enabled; after re-releasing lanes in Phase B the lanes re-train, so equalization may differ slightly between phases (see SNR/eye snapshots).",
        f"Reproduce: <font face='Courier' size='8'>.venv/bin/python crosstalk.py --octal {A} --lane {L} --rounds {cfg['rounds']} --dwell {cfg['dwell']}</font> from CSAK_EVB/webui/.",
    ]
    for s in notes:
        S.append(Paragraph("• " + s, body))
    if res.get("error"):
        S.append(Spacer(1, 6))
        S.append(Paragraph(f"<b>Run ended with error:</b> {res['error']}", body))

    doc.build(S, onFirstPage=footer, onLaterPages=footer)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--octal", required=True, help="victim octal, e.g. :8")
    ap.add_argument("--partner", default=None, help="partner octal (default: from board profile)")
    ap.add_argument("--lane", type=int, default=0, choices=range(8))
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--dwell", type=int, default=10, help="seconds per BER round (1-600)")
    ap.add_argument("--lock-timeout", type=int, default=60)
    ap.add_argument("--no-screenshots", action="store_true")
    ap.add_argument("--url", default="http://127.0.0.1:8321")
    a = ap.parse_args()
    cfg = {"octal": a.octal, "partner": a.partner, "lane": a.lane, "rounds": max(1, a.rounds),
           "dwell": min(600, max(1, a.dwell)), "lock_timeout": a.lock_timeout,
           "screenshots": not a.no_screenshots, "url": a.url}
    res = run(cfg)
    print(json.dumps(res["summary"], indent=1))
    print("report:", res["files"]["pdf"])


if __name__ == "__main__":
    main()
