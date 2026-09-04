"""Parsers for aapl CLI text output -> JSON-friendly dicts.

Each parser takes the raw stdout of one aapl invocation. Formats are pinned by
the fixtures in tests/fixtures/, all captured from the real board (ps2-659D,
AAPL 4.1.6, 2026-08-27).
"""
import re

# --------------------------------------------------------------------------
# ps2 -display -v 1
# --------------------------------------------------------------------------
_RAIL_RE = re.compile(
    r"^(?P<name>\S+)\s+(?P<idx>\d+):\s+(?P<state>ON|OFF)\s+"
    r"(?P<set_v>[\d.]+)V\s+(?P<limit_a>[\d.]+)A\s*\|\s*"
    r"(?P<meas_v>[\d.]+)V\s+(?P<lt><?)(?P<meas_a>[\d.]+)A\s*\|\s*"
    r"(?P<watts>[\d.]+)W")
_TOTAL_RE = re.compile(r"^Total:\s+(?P<total>[\d.]+)W")
_SERIAL_RE = re.compile(r"^Serial\s+(?P<serial>\d+)\s+(?P<in_v>[\d.]+)V\s+(?P<in_a>[\d.]+)A")


def parse_ps2_display(text):
    rails, total_w, serial, input_v, input_a = [], None, None, None, None
    for line in text.splitlines():
        line = line.rstrip()
        m = _SERIAL_RE.match(line)
        if m:
            serial = int(m.group("serial"))
            input_v, input_a = float(m.group("in_v")), float(m.group("in_a"))
            continue
        m = _RAIL_RE.match(line)
        if m:
            rails.append({
                "name": m.group("name"),
                "index": int(m.group("idx")),
                "on": m.group("state") == "ON",
                "set_v": float(m.group("set_v")),
                "limit_a": float(m.group("limit_a")),
                "meas_v": float(m.group("meas_v")),
                "meas_a": float(m.group("meas_a")),
                "meas_a_is_bound": m.group("lt") == "<",
                "watts": float(m.group("watts")),
                "fault": ("FLT" in line) or ("!PGOOD" in line),
            })
            continue
        m = _TOTAL_RE.match(line)
        if m:
            total_w = float(m.group("total"))
    return {
        "serial": serial,
        "input": {"volts": input_v, "amps": input_a},
        "rails": rails,
        "rails_on": sum(1 for r in rails if r["on"]),
        "any_fault": any(r["fault"] for r in rails),
        "total_watts": total_w,
        "powered": bool(rails) and all(r["on"] for r in rails),
    }


# --------------------------------------------------------------------------
# device-info
# --------------------------------------------------------------------------
_CHIP_RE = re.compile(r"^Information for chip (\d+):")
_JTAG_RE = re.compile(r"JTAG ID:\s*(\S+);\s*(.*?);\s*(\S+)\s*$")
_SERDES_RE = re.compile(
    r"^\s*(?P<addr>\S+)\s+SerDes Condor\s+(?P<rev>\S+)\s+\d+\s+"
    r"(?P<fw>0x[0-9A-Fa-f_]+)\s+(?P<on>\d)\s+(?P<info>.*)$")
_TEMP_IN_INFO_RE = re.compile(r"T=([\d.]+)C")
_THERMAL_RE = re.compile(r"^\s*(?P<addr>\S+)\s+Thermal Sensor\s+\S+\s+\((?P<temps>[^)]*)\)\s*\((?P<volts>[^)]*)\)")
_PLL_RE = re.compile(r"^\s*(?P<addr>\S+)\s+System PLL\s+.*lock=(?P<lock>\d)")
_PCS_RE = re.compile(r"^\s*(?P<addr>\S+)\s+SNPS PCS 1\.6T\s+\d+\s+(?P<info>.*)$")


def parse_device_info(text):
    chips, cur = [], None
    for line in text.splitlines():
        m = _CHIP_RE.match(line)
        if m:
            cur = {"chip": int(m.group(1)), "jtag_id": None, "chip_name": None,
                   "process": None, "serdes": [], "thermal": [], "pll": [], "pcs": []}
            chips.append(cur)
            continue
        if cur is None:
            continue
        m = _JTAG_RE.search(line)
        if m:
            cur["jtag_id"], cur["chip_name"], cur["process"] = m.group(1), m.group(2), m.group(3)
            continue
        m = _SERDES_RE.match(line)
        if m:
            info = m.group("info")
            tm = _TEMP_IN_INFO_RE.search(info)
            fw = m.group("fw")
            cur["serdes"].append({
                "addr": m.group("addr"),
                "rev": m.group("rev"),
                "firmware": fw,
                "fw_loaded": fw not in ("0x0000_00", "0x0000_0000"),
                "running": m.group("on") == "1",
                "temp_c": float(tm.group(1)) if tm else None,
                "info": info.strip(),
            })
            continue
        m = _THERMAL_RE.match(line)
        if m:
            # trailing unit letters live inside the parens: "(..., 39.2 C)" / "(..., 1.791 V)"
            temps = [float(t.strip().rstrip("CV").strip())
                     for t in m.group("temps").split(",") if t.strip()]
            volts = [float(v.strip().rstrip("CV").strip())
                     for v in m.group("volts").split(",") if v.strip()]
            cur["thermal"].append({
                "addr": m.group("addr"),
                "temps_c": [t for t in temps if t > -900],  # -1000 = unused channel
                "volts": [v for v in volts if v > 0],
            })
            continue
        m = _PLL_RE.match(line)
        if m:
            cur["pll"].append({"addr": m.group("addr"), "lock": m.group("lock") == "1"})
            continue
        m = _PCS_RE.match(line)
        if m:
            cur["pcs"].append({"addr": m.group("addr"), "info": m.group("info").strip()})
    return {"chips": chips}


# --------------------------------------------------------------------------
# condor -display-lane   (the "dsc" dump)
# --------------------------------------------------------------------------
_LANE_HDR_RE = re.compile(r"^\s*(?P<lane>\d+)\s*\((?P<txp>[+-])(?P<rxp>[+-])(?P<pam>[A-Za-z0-9]+)\s*,")
_SD_LCK_RE = re.compile(r"\)\s*(?P<sd>\d)\*?\s+(?P<lck>\d)\*?\s+(?P<rxppm>-?\d+)")
_PT_RE = re.compile(r"PT\(([^)]*)\)")
_TAIL_RE = re.compile(
    r"\(\s*(?P<eye_u>\d+),\s*(?P<eye_m>\d+),\s*(?P<eye_l>\d+)\)\s+"
    r"(?P<link_time>[\d.]+)\s+(?P<snr>[\d.]+)\s+(?P<ber_lt><?)(?P<ber>\S+)\s*$")
_CORE_RE = re.compile(r"^Core\s*=\s*(\S+);")
# After the TXPPM value the diag prints: pi-enable flag ('*' or ' '), a space,
# then 'D' if TX is disabled (tx_disable_status), 'P' if TX precoder is on,
# 'T' if TX link training is on, then the TXEQ "(" group.
_TX_FLAGS_RE = re.compile(r"\s(?P<txppm>-?\d+)[* ] (?P<dis> D|  )(?P<prec>[P ])(?P<train>[T ])\(")
_UCODE_RE = re.compile(r"Common Ucode Version\s*=\s*(\S+)")
_API_RE = re.compile(r"SERDES API Version\s*=\s*(\S+)")


# Head of each lane row: "0 (++P4E ,BRx1:x1, 0x4340, 0x00_0000, 0,0, 41 )" —
# the two small integers after UC_STS are RST and STP. Per the display-lane
# legend, RST is the TX/RX reset state {reset_active, reset_occurred,
# reset_held}: a running lane reads 0, a lane held in dp reset reads 7. The
# middle "occurred" bit is history, so "stopped" looks only at the outer two.
_RST_STP_RE = re.compile(
    r"^\s*(?P<lane>\d+)\s*\(\s*[+-][+-]\S*\s*,\s*\S+\s*,\s*0x[0-9a-fA-F]+\s*,"
    r"\s*0x[0-9a-fA-F_]+\s*,\s*(?P<rst>\d+)\s*,\s*(?P<stp>\d+)\s*,")


def parse_display_lane(text):
    core, ucode, api, lanes = None, None, None, []
    for line in text.splitlines():
        m = _CORE_RE.match(line.replace("  ", " ").strip())
        if m and core is None:
            core = m.group(1)
        m = _UCODE_RE.search(line)
        if m:
            ucode = m.group(1)
        m = _API_RE.search(line)
        if m:
            api = m.group(1)
        m = _LANE_HDR_RE.match(line)
        if not m:
            continue
        lane = {
            "lane": int(m.group("lane")),
            "tx_pol": m.group("txp"),
            "rx_pol": m.group("rxp"),
            "pam": m.group("pam"),
        }
        m1 = _RST_STP_RE.match(line)
        if m1:
            lane["dp_reset"] = int(m1.group("rst"))
            lane["uc_stopped"] = int(m1.group("stp"))
            lane["stopped"] = bool(lane["dp_reset"] & 0b101)
        m2 = _SD_LCK_RE.search(line)
        if m2:
            lane["signal_detect"] = m2.group("sd") == "1"
            lane["locked"] = m2.group("lck") == "1"
            lane["rx_ppm"] = int(m2.group("rxppm"))
        m3 = _PT_RE.search(line)
        if m3:
            try:
                lane["txeq"] = [int(x) for x in m3.group(1).split(",")]
            except ValueError:
                lane["txeq"] = None
        m5 = _TX_FLAGS_RE.search(line)
        if m5:
            lane["tx_ppm"] = int(m5.group("txppm"))
            lane["tx_disabled"] = m5.group("dis").strip() == "D"
            lane["tx_output"] = not lane["tx_disabled"]
            lane["tx_precoder"] = m5.group("prec") == "P"
            lane["tx_training"] = m5.group("train") == "T"
        m4 = _TAIL_RE.search(line)
        if m4:
            lane["eye"] = [int(m4.group("eye_u")), int(m4.group("eye_m")), int(m4.group("eye_l"))]
            lane["link_time_ms"] = float(m4.group("link_time"))
            lane["snr_db"] = float(m4.group("snr"))
            lane["ber"] = m4.group("ber")
            lane["ber_is_bound"] = m4.group("ber_lt") == "<"
        lanes.append(lane)
    return {"core": core, "ucode": ucode, "api": api, "lanes": lanes}


# --------------------------------------------------------------------------
# condor -display-core   (core/PLL state — the authoritative "was Core-init
# run?" signal; the per-lane RST/STP columns read 7,7 both before AND after
# Core-init, so they cannot tell the two states apart — verified 2026-09-03)
# --------------------------------------------------------------------------
_CORE_HDR_RE = re.compile(r"^\s*CORE\s+RST_ST\s+PLL_PWDN\s+UC_ATV")
_CORE_ROW_RE = re.compile(
    r"^\s*(?P<core>\d+)\s+(?P<rst>\d+),(?P<uc_sts>[0-9A-Fa-f]+)\s+(?P<pwdn>\d)\s+(?P<uc_atv>\d)\s+"
    r"(?P<com_clk>\S+)\s+(?P<ucode>\S+)\s+(?P<api>\S+)\s+(?P<afe>\S+)\s+"
    r"(?P<temp>-?\d+)C\s+\((?P<tmon>-?\d+)\)\s*(?P<avg_temp>-?\d+)C\s+(?P<rescal>\S+)\s+"
    r"(?P<vco>\S+)\s+(?P<vco_range>\d+)\s+(?P<pll_div>\S+)\s+(?P<lock>\d)(?P<lock_flag>\*?)")


def parse_display_core(text):
    """Parse the CORE row of `condor -display-core`.

    Returns state = "no_fw" (firmware not loaded: ucode verify fails and no
    CORE row is printed), "needs_core_init" (firmware running but PLL powered
    down / unlocked at its default divider), or "ready" (PLL locked, core dp
    reset released) — the precondition for Lane-init."""
    r = {"core": None, "ucode": None, "api": None, "pll_lock": False,
         "pll_pwdn": None, "uc_active": None, "dp_reset_state": None,
         "pll_div": None, "vco_rate": None, "com_clk": None, "temp_c": None,
         "ucode_verify_fail": "ERR_CODE_UCODE_VERIFY_FAIL" in text,
         "error": None, "state": "no_fw"}
    m = re.search(r"^ERROR:\s*(.*)$", text, re.M)
    if m:
        r["error"] = m.group(1).strip()
    m = _UCODE_RE.search(text)
    if m:
        r["ucode"] = m.group(1)
    m = _API_RE.search(text)
    if m:
        r["api"] = m.group(1)
    seen_hdr = False
    for line in text.splitlines():
        if _CORE_HDR_RE.match(line):
            seen_hdr = True
            continue
        if not seen_hdr:
            continue
        m = _CORE_ROW_RE.match(line)
        if not m:
            continue
        r.update({
            "core": int(m.group("core")),
            "dp_reset_state": int(m.group("rst")),
            "uc_status": m.group("uc_sts"),
            "pll_pwdn": int(m.group("pwdn")),
            "uc_active": int(m.group("uc_atv")),
            "com_clk": m.group("com_clk"),
            "ucode": m.group("ucode"),
            "api": m.group("api"),
            "temp_c": int(m.group("temp")),
            "vco_rate": m.group("vco"),
            "pll_div": m.group("pll_div"),
            "pll_lock": m.group("lock") == "1",
        })
        break
    if r["core"] is None or r["ucode_verify_fail"]:
        r["state"] = "no_fw"
    elif not r["pll_lock"] or r["pll_pwdn"] == 1 or r["uc_active"] != 1:
        r["state"] = "needs_core_init"
    else:
        r["state"] = "ready"
    return r


# --------------------------------------------------------------------------
# serdes -get-pmd-rx-lock
# --------------------------------------------------------------------------
_PMD_RE = re.compile(r"SBus\s+(?P<addr>\S+),\s+PMD RX Lock is\s+(?P<lock>\d)")


def parse_pmd_lock(text):
    out = {}
    for m in _PMD_RE.finditer(text):
        out[m.group("addr")] = m.group("lock") == "1"
    return out


# --------------------------------------------------------------------------
# serdes -ber
# --------------------------------------------------------------------------
_BER_ROW_RE = re.compile(
    r"^\s*(?P<addr>\S*:\S+)\s+@(?P<rate>[\d.]+)\s+(?P<dwell>[\d.]+)\s+"
    r"(?P<errors>\d+)\s+(?P<err_diff>\d+)\s+(?P<err_per_sec>\d+)\s+"
    r"(?P<lt><?)(?P<ber>[\d.eE+-]+)\s*$")


def parse_ber(text):
    rows = []
    for line in text.splitlines():
        m = _BER_ROW_RE.match(line)
        if not m:
            continue
        rate_gbps = float(m.group("rate"))
        dwell_s = float(m.group("dwell"))
        rows.append({
            "addr": m.group("addr"),
            "rate_gbps": rate_gbps,
            "dwell_s": dwell_s,
            "errors": int(m.group("errors")),
            "error_diff": int(m.group("err_diff")),
            "bits": int(rate_gbps * 1e9 * dwell_s),
            "ber": float(m.group("ber")),
            "ber_is_bound": m.group("lt") == "<",
        })
    return {"lanes": rows}
