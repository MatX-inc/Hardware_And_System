"""Offline parser tests against real captured board output. Run:
   .venv/bin/python -m tests.test_parsers        (from webui/)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import parsers

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


def test_ps2():
    r = parsers.parse_ps2_display(load("ps2_display.txt"))
    assert r["serial"] == 48879
    assert len(r["rails"]) == 14, f"expected 14 rails, got {len(r['rails'])}"
    assert r["rails_on"] == 14 and r["powered"] and not r["any_fault"]
    assert abs(r["total_watts"] - 21.804) < 1e-6
    vdd = r["rails"][0]
    assert vdd["name"] == "VDD075" and vdd["watts"] == 8.25 and not vdd["meas_a_is_bound"]
    assert r["rails"][2]["meas_a_is_bound"]  # TRVDD075-1 shows <0.150A
    print("ps2:        OK  (14 rails, 21.804 W)")


def test_device_info():
    r = parsers.parse_device_info(load("device_info.txt"))
    assert len(r["chips"]) == 2
    c0 = r["chips"][0]
    assert c0["jtag_id"] == "0x0ac4257f" and "Triple" in c0["chip_name"]
    assert len(c0["serdes"]) == 6, f"die0 serdes: {len(c0['serdes'])}"
    by = {s["addr"]: s for s in c0["serdes"]}
    assert by[":9"]["fw_loaded"] and by[":9"]["running"] and by[":9"]["firmware"] == "0xD003_07"
    assert not by[":8"]["fw_loaded"] and not by[":8"]["running"]
    assert by[":e"]["temp_c"] == 39.75
    assert c0["pll"][0]["lock"]
    assert any("pmd_rx_lock" in p["info"] for p in c0["pcs"])
    c1 = r["chips"][1]
    assert len(c1["serdes"]) == 6 and not any(s["fw_loaded"] for s in c1["serdes"])
    assert c1["thermal"][0]["temps_c"] == [34.5, 36.2, 35.2, 38.2, 39.2, 35.2]
    print("device-info: OK  (2 dice, 6+6 octals, fw state correct)")


def test_display_lane():
    r = parsers.parse_display_lane(load("display_lane_9.txt"))
    assert r["ucode"] == "D003_07" and r["api"] == "A00401"
    assert len(r["lanes"]) >= 8
    l0 = r["lanes"][0]
    assert l0["lane"] == 0 and l0["tx_pol"] == "+" and l0["rx_pol"] == "+"
    assert l0["signal_detect"] and l0["locked"]
    assert l0["snr_db"] == 21.95 and l0["ber"] == "1.4e-10" and l0["ber_is_bound"]
    assert l0["eye"] == [66, 66, 66] and l0["link_time_ms"] == 3681.2
    assert l0["txeq"] == [0, 0, -38, 130, 0, 0]
    assert l0["dp_reset"] == 0 and l0["uc_stopped"] == 0 and l0["stopped"] is False
    assert l0["tx_output"] is True and l0["tx_disabled"] is False and l0["tx_training"] is True
    # pre-lane-init row shape: "...    0   D  ( x , x , x ,168, x , x )"
    r2 = parsers.parse_display_lane(load("display_core_e_nocoreinit.txt"))
    assert len(r2["lanes"]) == 8 and all(l["tx_disabled"] and not l["tx_training"] for l in r2["lanes"])
    l4 = [l for l in r["lanes"] if l["lane"] == 4][0]
    assert l4["rx_pol"] == "-", "lane 4 should show RX polarity inverted"
    print("display-lane: OK  (8 lanes, polarity/SNR/eye/txeq extracted)")


def test_pmd_lock():
    r = parsers.parse_pmd_lock(load("pmd_lock.txt"))
    assert len(r) >= 16
    assert r[":9.0"] is True and r[":f.7"] is True
    print("pmd-lock:    OK  (%d lanes)" % len(r))


def test_ber():
    r = parsers.parse_ber(load("ber_output.txt"))
    assert len(r["lanes"]) == 16
    l0 = r["lanes"][0]
    assert l0["addr"] == ":9.0" and l0["errors"] == 0 and l0["ber_is_bound"]
    assert abs(l0["ber"] - 4.536e-13) < 1e-16
    assert l0["bits"] == int(212.504e9 * 10.374)
    l1 = r["lanes"][1]
    assert l1["addr"] == ":9.1" and l1["errors"] == 7 and not l1["ber_is_bound"]
    assert abs(l1["ber"] - 3.165e-12) < 1e-16
    print("ber:         OK  (16 lanes, bits/bound math checks)")


def test_display_core():
    ok = parsers.parse_display_core(load("display_core_f.txt"))
    assert ok["state"] == "ready", ok
    assert ok["core"] == 15 and ok["pll_lock"] and ok["pll_pwdn"] == 0 and ok["uc_active"] == 1
    assert ok["pll_div"] == "170" and ok["vco_rate"] == "106.25GHz" and ok["dp_reset_state"] == 0
    assert ok["ucode"] == "D003_07" and ok["api"] == "A00401" and ok["com_clk"] == "156.25MHz"
    nc = parsers.parse_display_core(load("display_core_e_nocoreinit.txt"))
    assert nc["state"] == "needs_core_init", nc
    assert nc["core"] == 14 and not nc["pll_lock"] and nc["pll_pwdn"] == 1 and nc["pll_div"] == "118"
    assert nc["dp_reset_state"] == 7 and nc["ucode"] == "D003_07"
    nf = parsers.parse_display_core(load("display_core_3_nofw.txt"))
    assert nf["state"] == "no_fw" and nf["ucode_verify_fail"] and nf["core"] is None
    assert "ERR_CODE_UCODE_VERIFY_FAIL" in (nf["error"] or "") or "ucode version" in (nf["error"] or "")
    print("display-core: OK  (ready / needs_core_init / no_fw states)")


if __name__ == "__main__":
    test_ps2()
    test_device_info()
    test_display_lane()
    test_display_core()
    test_pmd_lock()
    test_ber()
    print("\nall parser tests passed")
