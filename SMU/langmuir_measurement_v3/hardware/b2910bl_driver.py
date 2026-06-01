"""
Keysight B2910BL SMU Driver
===========================
Full SCPI driver for the Keysight B2910BL 1-channel precision Source
Measurement Unit.  Every SCPI command is commented inline with its
physical meaning so the code is self-documenting for lab engineers.

Device constraints enforced here:
    - ±210 V, ±1.5 A DC limits
    - DC-only (BL variant has NO pulse-mode capability)
    - 10 fA current resolution → floor used in electron-current filter
    - Minimum trigger interval: 50 µs
    - Internal trace buffer: up to 100 000 points
    - High Capacitance Mode available (:SENS:CURR:HCAP ON)
    - Configurable 4-wire / 2-wire remote sensing
"""

from __future__ import annotations

import time
import numpy as np
from typing import Tuple


class B2910BLDriver:
    """
    SCPI driver for the Keysight B2910BL Source Measurement Unit.

    This class is *hardware-only*: it configures the instrument, runs a
    DC linear voltage sweep and returns raw (V, I) arrays.  No physics
    analysis belongs here — see physics.langmuir_analysis for that.

    Parameters
    ----------
    instrument :
        Open VISA resource (pyvisa.Resource or MockInstrument).
    compliance_current : float
        Maximum current the SMU may source/sink (A).
        Default 0.1 A (100 mA) protects fragile plasma probes.
    nplc : float
        Integration time in Power Line Cycles.
        1 PLC ≈ 16.67 ms at 60 Hz.  Range: 0.001 – 10.
        Higher → less noise, slower sweep.
    high_cap_mode : bool
        Enables High Capacitance Mode (:SENS:CURR:HCAP ON).
        Use when driving capacitive loads (long coax, large probe areas)
        to prevent oscillation in the current measurement amplifier.
    remote_sensing : bool
        True  → 4-wire Kelvin sensing (:SYST:RSEN ON) — eliminates
                 lead-resistance error; preferred for accurate I(V).
        False → 2-wire local sensing — required when using a
                 multiplexer or relay matrix between SMU and probe.
    """

    # ------------------------------------------------------------------ #
    #  B2910BL hardware limits (hard-coded from datasheet)
    # ------------------------------------------------------------------ #
    VOLT_MAX: float = 210.0          # V   – absolute voltage range
    CURR_MAX: float = 1.5            # A   – absolute current range
    TRIG_INTERVAL_MIN: float = 50e-6 # s   – minimum inter-trigger delay
    BUFFER_MAX: int = 100_000        # pts – maximum trace-buffer depth

    def __init__(
        self,
        instrument,
        compliance_current: float = 0.1,
        nplc: float = 1.0,
        high_cap_mode: bool = False,
        remote_sensing: bool = False,
    ) -> None:
        self.inst = instrument
        self.compliance_current = compliance_current
        self.nplc = nplc
        self.high_cap_mode = high_cap_mode
        self.remote_sensing = remote_sensing

    # ================================================================== #
    #  Public API
    # ================================================================== #

    def identify(self) -> str:
        """Query and return the *IDN? identification string."""
        return self.inst.query("*IDN?").strip()

    def reset(self) -> None:
        """
        Restore factory defaults and clear the error/status queue.

        Always call reset() before a new measurement session so that
        lingering settings from a previous run cannot corrupt results.
        """
        self.inst.write("*RST")   # Factory reset — all user settings cleared
        self.inst.write("*CLS")   # Clear Status Byte and all event registers
        time.sleep(0.5)           # Give the firmware time to complete the reset

    def configure(self) -> None:
        """
        Apply all static instrument settings.

        Separating configuration from execution lets you verify settings
        (e.g. with :SYST:ERR? checks) before committing to a sweep.
        """
        self._configure_source()
        self._configure_measurement()
        self._configure_system()

    def run_sweep(
        self,
        v_start: float,
        v_stop: float,
        n_points: int,
        trigger_interval: float = 200e-6,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Execute a DC linear voltage sweep and return (V, I) arrays.

        The SMU ramps voltage linearly from *v_start* to *v_stop*,
        measuring current at each step.  Data are fetched from the
        instrument output buffer after the sweep completes.

        Parameters
        ----------
        v_start : float
            Sweep start voltage (V).  |v_start| ≤ 210 V.
        v_stop : float
            Sweep stop voltage (V).  |v_stop| ≤ 210 V.
        n_points : int
            Number of measurement points.  2 ≤ n_points ≤ 100 000.
        trigger_interval : float
            Minimum time between successive trigger events (s).
            Must be ≥ 50 µs (B2910BL hardware minimum).
            Default 200 µs gives stable measurements for typical probes.

        Returns
        -------
        V : np.ndarray   Voltage array (V), shape (n_points,)
        I : np.ndarray   Current array (A), shape (n_points,)

        Raises
        ------
        ValueError   If any parameter is outside the allowed range.
        RuntimeError If the instrument returns an unexpected data length.
        """
        self._validate_sweep_params(v_start, v_stop, n_points, trigger_interval)
        self._apply_sweep_source(v_start, v_stop, n_points)
        self._apply_sweep_trigger(n_points, trigger_interval)

        try:
            self.inst.write(":OUTP ON")          # Enable SMU output channel
            self.inst.write(":INIT")             # Arm trigger & start the sweep
            self._wait_for_completion()
            V, I = self._fetch_data(n_points)
        finally:
            self.inst.write(":OUTP OFF")         # Always disable output — safety critical

        return V, I

    # ================================================================== #
    #  Private — static configuration helpers
    # ================================================================== #

    def _configure_source(self) -> None:
        """Configure the voltage source for DC linear-sweep operation."""
        self.inst.write(":SOUR:FUNC:MODE VOLT")  # Source voltage (not current)
        self.inst.write(":SOUR:VOLT:MODE SWE")   # Use sweep mode (vs. fixed or list)
        self.inst.write(":SOUR:SWE:SPAC LIN")    # Linear (equidistant) step spacing
        self.inst.write(":SOUR:SWE:RANG BEST")   # Auto-select optimal voltage range
        self.inst.write(":SOUR:DEL:AUTO ON")      # Automatic source settling delay
        # NOTE: B2910BL (BL variant) does NOT support pulse mode — DC sweeps only

    def _configure_measurement(self) -> None:
        """Configure current sensing, compliance limit, and integration time."""
        self.inst.write(':SENS:FUNC "CURR"')                    # Sense current (not voltage or Ω)
        self.inst.write(":SENS:CURR:RANG:AUTO ON")               # Auto-range current measurement
        self.inst.write(f":SENS:CURR:NPLC {self.nplc:.4g}")      # Integration: 1 PLC = 16.67 ms
                                                                  # Longer NPLC → lower noise floor
        self.inst.write(f":SENS:CURR:PROT {self.compliance_current:.6g}")
        # Current compliance (hard limit) — SMU forces output off if exceeded.
        # 100 mA default protects probe filaments and plasma equilibrium.

        hcap = "ON" if self.high_cap_mode else "OFF"
        self.inst.write(f":SENS:CURR:HCAP {hcap}")
        # High Capacitance Mode: reduces measurement bandwidth to prevent
        # ringing when the probe/cable presents a large capacitive load (> ~10 nF).

    def _configure_system(self) -> None:
        """Apply system-level settings: remote sense and data format."""
        rsen = "ON" if self.remote_sensing else "OFF"
        self.inst.write(f":SYST:RSEN {rsen}")
        # Remote Sense ON  → Kelvin 4-wire: SMU compensates for lead resistance.
        # Remote Sense OFF → 2-wire: required when a relay/mux is in the signal path.

        self.inst.write(":FORM:ELEM:SENS VOLT,CURR")
        # Request only voltage + current per data point.
        # Excludes resistance, timestamp, and status word to halve transfer time.

    # ================================================================== #
    #  Private — sweep execution helpers
    # ================================================================== #

    def _apply_sweep_source(self, v_start: float, v_stop: float, n_points: int) -> None:
        """Program sweep limits and step count into the source subsystem."""
        self.inst.write(f":SOUR:VOLT:STAR {v_start:.6g}")  # First voltage step
        self.inst.write(f":SOUR:VOLT:STOP {v_stop:.6g}")   # Last voltage step
        self.inst.write(f":SOUR:VOLT:POIN {n_points:d}")   # Total number of steps

    def _apply_sweep_trigger(self, n_points: int, trigger_interval: float) -> None:
        """Configure the trigger subsystem for fully automated sweep capture."""
        self.inst.write(":TRIG:SOUR AINT")                       # Automatic internal trigger
        self.inst.write(f":TRIG:DEL {trigger_interval:.6g}")     # Delay ≥ 50 µs between triggers
        self.inst.write(f":TRIG:ALL:COUN {n_points:d}")          # Sync source + measure, n_points triggers

    def _wait_for_completion(self, timeout_s: float = 600.0) -> None:
        """
        Block until the SMU reports *OPC (Operation Complete).

        *OPC? returns '1' only after all pending operations finish.
        This is more reliable than a fixed sleep and handles variable
        sweep durations correctly.
        """
        old_timeout = self.inst.timeout
        self.inst.timeout = int(timeout_s * 1000)   # pyvisa timeout in ms
        try:
            self.inst.query("*OPC?")                # Blocks until sweep + readback complete
        finally:
            self.inst.timeout = old_timeout

    def _fetch_data(self, n_points: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        Retrieve measurement data from the instrument output buffer.
 
        :FETC? returns all buffered data as a comma-separated ASCII string.
        With :FORM:ELEM:SENS VOLT,CURR the interleaved format is:
            V₁, I₁, V₂, I₂, … Vₙ, Iₙ
        """
        raw = self.inst.query(":FETC?")
        values = np.fromstring(raw, dtype=float, sep=",")
 
        if len(values) < 2:
            raise RuntimeError(
                f":FETC? returned only {len(values)} value(s). "
                "Expected at least 2 (one V,I pair)."
            )
 
        V = values[0::2][:n_points]
        I = values[1::2][:n_points]
        return V, I
    # ================================================================== #
    #  Private — input validation
    # ================================================================== #

    def _validate_sweep_params(
        self,
        v_start: float,
        v_stop: float,
        n_points: int,
        trig_interval: float,
    ) -> None:
        """Raise ValueError for any out-of-spec parameter before touching hardware."""
        if abs(v_start) > self.VOLT_MAX:
            raise ValueError(
                f"v_start={v_start} V exceeds B2910BL limit ±{self.VOLT_MAX} V"
            )
        if abs(v_stop) > self.VOLT_MAX:
            raise ValueError(
                f"v_stop={v_stop} V exceeds B2910BL limit ±{self.VOLT_MAX} V"
            )
        if not (2 <= n_points <= self.BUFFER_MAX):
            raise ValueError(
                f"n_points={n_points} is outside allowed range [2, {self.BUFFER_MAX}]"
            )
        if trig_interval < self.TRIG_INTERVAL_MIN:
            raise ValueError(
                f"trigger_interval={trig_interval*1e6:.1f} µs is below the "
                f"B2910BL minimum of {self.TRIG_INTERVAL_MIN*1e6:.0f} µs"
            )
        if self.compliance_current > self.CURR_MAX:
            raise ValueError(
                f"compliance_current={self.compliance_current} A exceeds "
                f"B2910BL maximum {self.CURR_MAX} A"
            )
