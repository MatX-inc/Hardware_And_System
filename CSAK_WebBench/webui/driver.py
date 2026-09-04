"""Serialized wrapper around the aapl CLI.

All hardware access goes through one lock (AACS is effectively single-client),
every invocation is recorded in a command log, and when the board is
unreachable the driver serves captured fixtures so the UI stays demoable.
"""
import json
import os
import socket
import subprocess
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "tests", "fixtures")


def load_profile():
    with open(os.path.join(HERE, "board_profile.json")) as f:
        return json.load(f)


class AaplDriver:
    def __init__(self, profile=None, offline=None):
        self.profile = profile or load_profile()
        self.server = self.profile["server"]
        self.port = self.profile.get("aacs_port", 90)
        self.aapl = os.path.normpath(os.path.join(HERE, self.profile["aapl_binary"]))
        self._lock = threading.Lock()          # one hardware command at a time
        self._log = []                          # ring buffer of invocations
        self._log_lock = threading.Lock()
        self._forced_offline = offline          # None = auto-detect per call
        self._last_reach = (0.0, False)         # (checked_at, reachable)

    # -- reachability -----------------------------------------------------
    def _probe(self, attempts=3, timeout=1.5):
        # The AACS server is single-client and slow to re-accept, so one
        # connect attempt times out spuriously; retry before declaring down.
        for i in range(attempts):
            try:
                with socket.create_connection((self.server, self.port), timeout=timeout):
                    return True
            except OSError:
                if i + 1 < attempts:
                    time.sleep(0.4)
        return False

    def board_reachable(self, max_age_s=5.0):
        now = time.time()
        ts, ok = self._last_reach
        # a success verdict is durable; a failure verdict expires quickly
        if now - ts < (30.0 if ok else max_age_s):
            return ok
        if self._lock.locked():
            # an aapl command is mid-flight on the AACS link; probing now
            # would contend with it, so keep the previous verdict
            return ok
        ok = self._probe()
        self._last_reach = (now, ok)
        return ok

    def offline(self):
        if self._forced_offline is not None:
            return self._forced_offline
        return not self.board_reachable()

    # -- command log -------------------------------------------------------
    def _record(self, argv, output, dur, source, kind="query"):
        short = list(argv)
        if short and os.path.basename(short[0]) == "aapl":
            short[0] = "aapl"          # drop the long binary path in the log
        entry = {
            "ts": time.strftime("%H:%M:%S"),
            "cmd": " ".join(short),
            "duration_s": round(dur, 2),
            "source": source,               # "board" | "fixture" | "local"
            "kind": kind,                   # "query" (read/display only) | "action" (changes state)
            "output": output[-8000:],       # cap stored output
        }
        with self._log_lock:
            self._log.append(entry)
            if len(self._log) > 200:
                self._log.pop(0)
        return entry

    def log(self):
        with self._log_lock:
            return list(self._log)

    def clear_log(self):
        """Empty the in-memory command log (nothing on the board is touched)."""
        with self._log_lock:
            n = len(self._log)
            self._log.clear()
        return n

    # -- fixture routing (offline demo mode) --------------------------------
    _FIXTURE_MAP = [
        (("ps2",), "ps2_display.txt"),
        (("device-info",), "device_info.txt"),
        (("-display-lane",), "display_lane_9.txt"),
        (("-display-core",), "display_core_f.txt"),
        (("-get-pmd-rx-lock",), "pmd_lock.txt"),
        (("-ber",), "ber_output.txt"),
    ]

    def _fixture_for(self, argv):
        joined = " ".join(argv)
        for keys, fname in self._FIXTURE_MAP:
            if all(k in joined for k in keys):
                path = os.path.join(FIXTURES, fname)
                if os.path.exists(path):
                    with open(path) as f:
                        return f.read()
        return "(no fixture for this command)"

    # -- core runner ---------------------------------------------------------
    def run(self, args, timeout=60, kind="query"):
        """Run one aapl subcommand; args is the list after the binary name."""
        argv = [self.aapl] + list(args) + ["-server", self.server]
        t0 = time.time()
        if self.offline():
            out = self._fixture_for(argv)
            entry = self._record(argv, out, time.time() - t0, "fixture", kind)
            return out, entry
        with self._lock:
            try:
                p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
                out = p.stdout + (("\n" + p.stderr) if p.stderr.strip() else "")
                if p.returncode == 0:
                    # a completed board command is better reachability
                    # evidence than any probe
                    self._last_reach = (time.time(), True)
            except subprocess.TimeoutExpired:
                out = f"(timeout after {timeout}s)"
        entry = self._record(argv, out, time.time() - t0, "board", kind)
        return out, entry

    # -- phase-2 control operations -------------------------------------------
    # These refuse to run in offline/demo mode: fixtures must never masquerade
    # as a successful hardware action.

    def _control(self, args, timeout):
        if self.offline():
            return None, {"error": "board unreachable — demo mode is read-only"}
        out, entry = self.run(args, timeout=timeout, kind="action")
        return out, entry

    def ps2_load(self):
        """Sequenced power-up of all DUT rails (CSAK profile)."""
        return self._control(["ps2", "-load", "CSAK", "-display", "-v", "1"], timeout=180)

    def ps2_seq_off(self):
        """Sequenced power-down of all DUT rails."""
        return self._control(["ps2", "-seq-off"], timeout=120)

    def firmware_path(self):
        return os.path.normpath(os.path.join(HERE, self.profile["firmware_file"]))

    def fw_upload(self, octal_addr):
        return self._control(
            ["serdes", "-firmware-file", self.firmware_path(), "-upload",
             "-addr", octal_addr], timeout=120)

    def _octal_cfg(self, octal_addr):
        for die in self.profile["dies"]:
            for o in die["octals"]:
                if o["addr"] == octal_addr:
                    return o
        return None

    def core_init(self, octal_addr):
        ci = self.profile["core_init"]
        args = ["serdes-init", "-core-init",
                "-refclk", ci["refclk"], "-comclk", ci["comclk"], "-div", ci["divider"]]
        cfg = self._octal_cfg(octal_addr) or {}
        if cfg.get("tx_lane_map"):
            args += ["-tx-lane-map", cfg["tx_lane_map"], "-rx-lane-map", "76543210"]
        args += ["-addr", octal_addr]
        return self._control(args, timeout=90)

    def lane_init(self, octal_addr, lane=None):
        target = f"{octal_addr}.{lane}" if lane is not None else f"{octal_addr}.*"
        args = ["serdes-init"] + list(self.profile["lane_init_args"]) + ["-addr", target]
        return self._control(args, timeout=120)

    def tx_output(self, octal_addr, lane, enable):
        target = f"{octal_addr}.{lane}"
        return self._control(
            ["serdes", "-tx-output-enable", "1" if enable else "0", "-addr", target],
            timeout=30)

    def lane_dp_reset(self, octal_addr, lane, assert_reset):
        """Hold (1) or release (0) one lane's RX+TX datapath reset.
        Asserting is the per-lane "Stop": the lane keeps its configuration and
        the octal firmware keeps running, but the datapath is parked exactly as
        lane-init leaves it before configuring. Release (or re-run lane-init)
        to start it again."""
        target = f"{octal_addr}.{lane}"
        return self._control(
            ["serdes", "-lane-dp-reset", "1" if assert_reset else "0", "-addr", target],
            timeout=30)

    def octal_por_reset(self, octal_addr):
        """Pulse the octal's power-on-reset pin (blackhawk -toggle-POR).
        Wipes the volatile firmware and all lane state for that octal only, so
        it returns to the pre-upload state: fw 0x0000_00, not running. Follow
        with Upload FW -> Core-init -> Lane-init."""
        return self._control(["blackhawk", "-toggle-POR", "-addr", octal_addr], timeout=30)

    def arp_fix(self):
        """Delete the macOS proxy-ARP entry that blackholes IPv4 to the board
        after a hard power cycle (the entry binds the board IP to the Mac's
        own MAC). Local command, deliberately allowed while offline — that is
        precisely when it is needed. Requires a NOPASSWD sudoers rule for
        exactly this command; returns setup instructions if the rule is absent.
        """
        argv = ["sudo", "-n", "/usr/sbin/arp", "-d", self.server]
        t0 = time.time()
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=10)
            out = (p.stdout + p.stderr).strip()
            ok = p.returncode == 0
        except Exception as e:  # noqa: BLE001 - report any failure to the UI
            out, ok = str(e), False
        self._record(argv, out, time.time() - t0, "local", "action")
        if not ok and "password" in out.lower():
            user = os.environ.get("USER", "$USER")
            return {"ok": False, "error":
                    "sudo rule missing — run this once in a terminal:\n"
                    f"echo '{user} ALL=(root) NOPASSWD: /usr/sbin/arp -d {self.server}'"
                    " | sudo tee /etc/sudoers.d/csak-arp"}
        if "no entry" in out:
            return {"ok": True, "output": "no stale ARP entry present — nothing to fix"}
        if ok:
            self._last_reach = (0.0, False)   # force an immediate re-probe
            return {"ok": True, "output": out or f"ARP entry for {self.server} deleted"}
        return {"ok": False, "error": out}

    # -- phase-1 read-only conveniences ---------------------------------------
    def ps2_display(self):
        return self.run(["ps2", "-display", "-v", "1"], timeout=30)[0]

    def device_info(self):
        return self.run(["device-info"], timeout=90)[0]

    def display_lane(self, octal_addr):
        return self.run(["condor", "-display-lane", "-a", f"{octal_addr}.*"], timeout=60)[0]

    def display_core(self, octal_addr):
        """Core/PLL status of one octal (condor -display-core). Cheap (~0.5 s);
        its CORE row is the only reliable 'has Core-init been run?' signal."""
        return self.run(["condor", "-display-core", "-addr", octal_addr], timeout=60)[0]

    def ber(self, octal_addr, dwell=10, lane=None):
        """Timed PRBS BER measurement: reset error counters, dwell, report.
        lane=None measures all 8 lanes of the octal; an int measures one lane."""
        target = f"{octal_addr}.{lane}" if lane is not None else f"{octal_addr}.*"
        return self.run(["serdes", "-sbus-rings", "1", "-ber", "-ber-dwell",
                         str(dwell), "-error-reset", "-a", target],
                        timeout=dwell + 60, kind="action")[0]

    def pmd_lock(self, octal_addr):
        return self.run(["serdes", "-sbus-rings", "1", "-get-pmd-rx-lock",
                         "-a", f"{octal_addr}.*"], timeout=30)[0]
