"""
Connection Manager — Hybrid VISA Connection with Simulation Fallback
=====================================================================
Implements three connection strategies in order of priority:

  1. **Explicit address** — caller provides a VISA string directly.
  2. **Auto-detect** — scans pyvisa.ResourceManager().list_resources()
     and identifies likely B2910B/BL instruments by USB VID/PID.
  3. **Manual input** — CLI prompt if auto-detect finds nothing.
  4. **Simulation** — generates synthetic Langmuir I-V data; no hardware
     required.  Activated with ``simulate=True`` or when no VISA backend
     is found.

Simulation mode is essential for:
  - Offline algorithm development and testing
  - Demonstrating the analysis pipeline without lab access
  - CI/CD pipelines and automated regression tests

VISA address format examples:
  USB0::0x0957::0x8B18::MY12345678::INSTR   (USB, typical B2910BL)
  GPIB0::23::INSTR                           (GPIB)
  TCPIP0::192.168.1.10::inst0::INSTR         (LAN/VXI-11)
"""

from __future__ import annotations

import sys
import numpy as np
from typing import Optional


# ------------------------------------------------------------------ #
#  Keysight B2910BL USB identifiers (from USB descriptor)
# ------------------------------------------------------------------ #
_KEYSIGHT_VID = "0x0957"   # Keysight / Agilent vendor ID
_B2900_PID = "0x8B18"      # B2900 series product ID

# ------------------------------------------------------------------ #
#  Synthetic I-V curve physics parameters (simulation defaults)
# ------------------------------------------------------------------ #
_SIM_V_FLOAT = 2.0        # V   – floating potential
_SIM_V_PLASMA = 10.0      # V   – plasma potential
_SIM_T_E = 3.0            # eV  – electron temperature
_SIM_I_ION_SAT = -5e-3    # A   – ion saturation current  (< 0)
_SIM_I_E_SAT = 50e-3      # A   – electron saturation current (> 0)
_SIM_NOISE_FRACTION = 0.02 # –   – Gaussian noise as fraction of |I_ion_sat|


class ConnectionManager:
    """
    Manages the lifecycle of a VISA instrument connection.

    Parameters
    ----------
    simulate : bool
        Skip all hardware and use a MockInstrument instead.
    visa_backend : str
        pyvisa backend string, e.g. '' (NI-VISA) or '@py' (pyvisa-py).
        Ignored when simulate=True.

    Examples
    --------
    >>> mgr = ConnectionManager(simulate=True)
    >>> inst = mgr.connect()
    >>> print(inst.query("*IDN?"))

    >>> mgr = ConnectionManager()
    >>> inst = mgr.connect(address="USB0::0x0957::0x8B18::MY00001::INSTR")
    """

    def __init__(self, simulate: bool = False, visa_backend: str = "") -> None:
        self.simulate = simulate
        self.visa_backend = visa_backend
        self._rm = None
        self._instrument = None

    # ================================================================== #
    #  Public API
    # ================================================================== #

    def connect(self, address: Optional[str] = None):
        """
        Open and return an instrument handle.

        Parameters
        ----------
        address : str, optional
            VISA resource string.  If *None*, auto-detect is attempted first,
            then CLI prompt as fallback.

        Returns
        -------
        Instrument handle (pyvisa.Resource or MockInstrument).
        The returned object always implements .write(), .query(), .close().
        """
        if self.simulate:
            print("[ConnectionManager] Simulation mode — no hardware used.")
            self._instrument = MockInstrument()
            return self._instrument

        try:
            import pyvisa  # type: ignore
        except ImportError:
            print(
                "[ConnectionManager] WARNING: pyvisa not installed. "
                "Falling back to simulation mode.\n"
                "Install with:  pip install pyvisa pyvisa-py"
            )
            self._instrument = MockInstrument()
            return self._instrument

        self._rm = pyvisa.ResourceManager(self.visa_backend)

        if address is not None:
            resolved = address
        else:
            resolved = self._auto_detect() or self._prompt_user()

        if resolved is None:
            raise ConnectionError(
                "No VISA address available. "
                "Provide --address or run with --simulate."
            )

        print(f"[ConnectionManager] Connecting to: {resolved}")
        self._instrument = self._rm.open_resource(resolved)
        self._instrument.timeout = 30_000    # 30 s default; overridden during sweeps
        self._instrument.read_termination = "\n"
        self._instrument.write_termination = "\n"
        return self._instrument

    def disconnect(self) -> None:
        """Close the instrument connection gracefully."""
        if self._instrument is not None:
            try:
                self._instrument.close()
            except Exception:
                pass
            self._instrument = None
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:
                pass
            self._rm = None

    # ================================================================== #
    #  Private — connection strategies
    # ================================================================== #

    def _auto_detect(self) -> Optional[str]:
        """
        Scan all VISA resources for a likely B2910BL.

        Matches resources containing the Keysight VID (0x0957) and the
        B2900-series PID (0x8B18) in the resource string.
        """
        try:
            resources = self._rm.list_resources()
        except Exception as exc:
            print(f"[ConnectionManager] list_resources() failed: {exc}")
            return None

        candidates = [
            r for r in resources
            if _KEYSIGHT_VID in r.upper() or _B2900_PID in r.upper()
            or "B2910" in r.upper() or "B2900" in r.upper()
        ]

        if len(candidates) == 1:
            print(f"[ConnectionManager] Auto-detected: {candidates[0]}")
            return candidates[0]

        if len(candidates) > 1:
            print("[ConnectionManager] Multiple B2910BL candidates found:")
            for i, r in enumerate(candidates):
                print(f"  [{i}] {r}")
            while True:
                choice = input("Select index: ").strip()
                if choice.isdigit() and int(choice) < len(candidates):
                    return candidates[int(choice)]
                print("Invalid selection — try again.")

        # No B2910BL found — list all resources for context
        if resources:
            print("[ConnectionManager] Available VISA resources (no B2910BL detected):")
            for r in resources:
                print(f"  {r}")
        else:
            print("[ConnectionManager] No VISA resources found.")

        return None

    def _prompt_user(self) -> Optional[str]:
        """Fallback: ask the user to type a VISA address at the terminal."""
        print("\n[ConnectionManager] Manual VISA address entry required.")
        print("Example: USB0::0x0957::0x8B18::MY12345678::INSTR")
        addr = input("Enter VISA address (or press Enter to use simulation): ").strip()
        return addr if addr else None


# ====================================================================== #
#  Mock Instrument — synthetic Langmuir probe I-V curve generator
# ====================================================================== #

class MockInstrument:
    """
    Drop-in replacement for a pyvisa.Resource that synthesises a
    realistic Langmuir probe I-V characteristic.

    The mock intercepts :SOUR:VOLT:STAR / :STOP / :POIN write commands
    to learn the requested sweep range, then generates synthetic data
    (with Gaussian noise) when :FETC? is queried.

    Plasma physics parameters can be adjusted via constructor keyword
    arguments for sensitivity / algorithm testing.

    Parameters
    ----------
    v_float  : float   Floating potential (V)
    v_plasma : float   Plasma potential (V)
    T_e      : float   Electron temperature (eV)
    i_ion_sat : float  Ion saturation current (A, negative)
    i_e_sat  : float   Electron saturation current (A, positive)
    noise_fraction : float  Noise amplitude as fraction of |i_ion_sat|
    seed     : int | None   Random seed for reproducibility
    """

    def __init__(
        self,
        v_float: float = _SIM_V_FLOAT,
        v_plasma: float = _SIM_V_PLASMA,
        T_e: float = _SIM_T_E,
        i_ion_sat: float = _SIM_I_ION_SAT,
        i_e_sat: float = _SIM_I_E_SAT,
        noise_fraction: float = _SIM_NOISE_FRACTION,
        seed: Optional[int] = 42,
    ) -> None:
        self.timeout = 10_000

        # Store physics parameters
        self._v_float = v_float
        self._v_plasma = v_plasma
        self._T_e = T_e
        self._i_ion_sat = i_ion_sat
        self._i_e_sat = i_e_sat
        self._noise_fraction = noise_fraction
        self._rng = np.random.default_rng(seed)

        # Sweep parameters filled in by write() calls
        self._sweep: dict = {
            "v_start": -50.0,
            "v_stop": 50.0,
            "n_points": 1000,
        }

    # ------------------------------------------------------------------ #
    #  VISA-compatible interface
    # ------------------------------------------------------------------ #

    def write(self, command: str) -> None:
        """Parse SCPI write commands and cache sweep parameters."""
        cmd = command.strip()
        cmd_upper = cmd.upper()

        if ":SOUR:VOLT:STAR" in cmd_upper:
            self._sweep["v_start"] = float(cmd.split()[-1])
        elif ":SOUR:VOLT:STOP" in cmd_upper:
            self._sweep["v_stop"] = float(cmd.split()[-1])
        elif ":SOUR:VOLT:POIN" in cmd_upper:
            self._sweep["n_points"] = int(float(cmd.split()[-1]))
        # All other commands accepted silently (configuration, output on/off, etc.)

    def query(self, command: str) -> str:
        """Return appropriate SCPI query responses."""
        cmd = command.strip().upper()

        if "*IDN?" in cmd:
            return "Keysight Technologies,B2910BL,MY00000000,1.0.2024.0101 [SIMULATED]"
        if "*OPC?" in cmd:
            return "1"
        if ":FETC?" in cmd:
            return self._generate_fetch_response()
        if ":SYST:ERR?" in cmd:
            return '+0,"No Error"'
        return "0"

    def close(self) -> None:
        """No-op — nothing to close in simulation."""
        pass

    # ------------------------------------------------------------------ #
    #  Private — synthetic I-V curve generation
    # ------------------------------------------------------------------ #

    def _generate_fetch_response(self) -> str:
        """
        Generate a realistic Langmuir probe I-V curve and format it as a
        comma-separated string matching the :FORM:ELEM:SENS VOLT,CURR format:
            V1, I1, V2, I2, ...
        """
        v_start = self._sweep["v_start"]
        v_stop = self._sweep["v_stop"]
        n = self._sweep["n_points"]

        V, I = self._compute_iv(np.linspace(v_start, v_stop, n))
        pairs = np.empty(2 * n)
        pairs[0::2] = V
        pairs[1::2] = I
        return ",".join(f"{x:.8e}" for x in pairs)

    def _compute_iv(self, V: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Compute the synthetic I(V) curve.

        Model:
          I_ion(V)  = I_ion_sat * (1 − α·(V − V_fl))     [sheath expansion slope]
          I_e(V)    = I_e_sat  * exp((V − V_p) / T_e)    for V < V_p
          I_e(V)    = I_e_sat  * (1 + β·(V − V_p))       for V ≥ V_p (electron sat.)
          I(V)      = I_ion(V) + I_e(V) + ε              [ε = Gaussian noise]
        """
        I_ion = self._i_ion_sat * (1.0 - 0.004 * (V - self._v_float))

        I_e = np.where(
            V < self._v_plasma,
            self._i_e_sat * np.exp(
                np.clip((V - self._v_plasma) / self._T_e, -80, 0)
            ),
            self._i_e_sat * (1.0 + 0.012 * (V - self._v_plasma)),
        )

        noise_std = self._noise_fraction * abs(self._i_ion_sat)
        noise = self._rng.normal(0.0, noise_std, len(V))

        return V, I_ion + I_e + noise
