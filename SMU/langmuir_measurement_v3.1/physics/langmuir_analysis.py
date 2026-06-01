"""
Langmuir Probe Physics Analysis Engine
=======================================
Implements the standard single Langmuir probe analysis for a planar probe
in the thin-sheath / OML regime.

Analysis pipeline (in order):
  1. Savitzky-Golay smoothing           → I_smooth
  2. Floating potential  V_fl           → zero crossing of I_smooth
  3. Differential conductance  dI/dV   → np.gradient
  4. Plasma potential    V_p            → max(dI/dV)
  5. Ion saturation fit  I_ion(V)      → linear fit in strongly negative bias
  6. Electron temperature  T_e [eV]    → slope of ln(I_e) vs V in [V_fl, V_p]
  7. Electron saturation   I_e_sat     → linear fit for V > V_p (sheath expansion)

Physical background:
  In the transition region (V_fl ≤ V ≤ V_p) the electron current follows
  a retarded Maxwellian distribution:
      I_e(V) = I_e_sat · exp((V − V_p) / T_e)
  Taking the natural logarithm gives a straight line with slope 1/T_e [eV⁻¹].

References:
  Lieberman & Lichtenberg, "Principles of Plasma Discharges", 2nd ed., §2.3
  Merlino, Am. J. Phys. 75, 1078 (2007)
"""

from __future__ import annotations

import warnings
import numpy as np
from scipy.signal import savgol_filter  # type: ignore
from dataclasses import dataclass, field
from typing import Optional, Tuple


# ====================================================================== #
#  Custom exception
# ====================================================================== #

class AnalysisError(RuntimeError):
    """Raised when the physics analysis cannot produce a reliable result."""


# ====================================================================== #
#  Result data class
# ====================================================================== #

@dataclass
class LangmuirResults:
    """
    Container for all extracted plasma parameters.

    All currents in Ampere; all voltages in Volt; T_e in eV.
    """
    V_fl: float           # Floating potential (V)  — where net I = 0
    V_p: float            # Plasma potential  (V)  — where dI/dV is maximum
    T_e: float            # Electron temperature (eV)
    I_ion_sat: float      # Ion saturation current (A, < 0)
    I_e_sat: float        # Electron saturation current (A, > 0)
    poly_ion: np.ndarray  # Coefficients of ion linear fit [slope, intercept]
    poly_esat: Optional[np.ndarray]   # Coefficients of electron-sat fit (None if V > V_p is sparse)
    poly_te: np.ndarray   # Coefficients of ln(I_e) linear fit
    ln_Ie: np.ndarray     # ln(I_electron) in the transition region
    V_te_region: np.ndarray  # Voltage points used for T_e fit

    def as_dict(self) -> dict:
        """Return scalar results as a plain dictionary (excludes arrays)."""
        return {
            "V_fl_V":         self.V_fl,
            "V_p_V":          self.V_p,
            "T_e_eV":         self.T_e,
            "I_ion_sat_A":    self.I_ion_sat,
            "I_e_sat_A":      self.I_e_sat,
        }

    def summary(self) -> str:
        """Human-readable summary string."""
        lines = [
            "=" * 40,
            "  Langmuir Probe Analysis Results",
            "=" * 40,
            f"  Floating potential  V_fl   = {self.V_fl:+.3f} V",
            f"  Plasma potential    V_p    = {self.V_p:+.3f} V",
            f"  Electron temperature T_e   =  {self.T_e:.3f} eV",
            f"  Ion sat. current   I_ion   =  {self.I_ion_sat*1e3:.3f} mA",
            f"  Electron sat.      I_e_sat =  {self.I_e_sat*1e3:.3f} mA",
            "=" * 40,
        ]
        return "\n".join(lines)


# ====================================================================== #
#  Main analyser class
# ====================================================================== #

class LangmuirAnalyzer:
    """
    Step-by-step physics analysis for a planar Langmuir probe I-V curve.

    Parameters
    ----------
    V : array-like
        Voltage array (V), monotonically increasing.
    I : array-like
        Measured current array (A), same length as V.
    savgol_window : int
        Savitzky-Golay filter window length.  Must be odd and > polyorder.
        Larger → smoother but risks smearing sharp features near V_p.
        Default 51 is good for 1000-point sweeps.
    savgol_polyorder : int
        Polynomial order for Savitzky-Golay filter.  Default 3.
    ion_fit_range : (float, float)
        Voltage range (V_min, V_max) used to fit the ion saturation current.
        Should be deep in the ion-saturation plateau — well below V_fl.
        Default (-50, -30) V; automatically clipped to the actual sweep range.

    Usage
    -----
    >>> analyzer = LangmuirAnalyzer(V, I)
    >>> results = analyzer.analyze()
    >>> print(results.summary())
    """

    # 10 fA current resolution of the B2910BL — used as noise floor
    CURR_NOISE_FLOOR: float = 10e-15  # A

    def __init__(
        self,
        V: np.ndarray,
        I: np.ndarray,
        savgol_window: int = 51,
        savgol_polyorder: int = 3,
        ion_fit_range: Tuple[float, float] = (-50.0, -30.0),
    ) -> None:
        self.V = np.asarray(V, dtype=float)
        self.I = np.asarray(I, dtype=float)
        self.savgol_window = savgol_window
        self.savgol_polyorder = savgol_polyorder
        self.ion_fit_range = ion_fit_range

        if self.V.shape != self.I.shape:
            raise ValueError(
                f"V and I must have the same shape, got {self.V.shape} vs {self.I.shape}"
            )
        if len(self.V) < 10:
            raise ValueError("Too few data points — need at least 10 for analysis.")

        # Internal working arrays filled by the pipeline
        self.I_smooth: Optional[np.ndarray] = None
        self.dIdV: Optional[np.ndarray] = None
        self.I_electron: Optional[np.ndarray] = None

    # ================================================================== #
    #  Public — main analysis entry point
    # ================================================================== #

    def analyze(self) -> LangmuirResults:
        """
        Execute the full analysis pipeline and return a LangmuirResults object.

        Steps are numbered to match the module docstring.

        Returns
        -------
        LangmuirResults
            All extracted plasma parameters plus intermediate arrays for plotting.

        Raises
        ------
        AnalysisError
            If a physically meaningful result cannot be extracted from the data.
        """
        # Step 1 — Smooth
        self.I_smooth = self._smooth()

        # Step 2 — Floating potential
        V_fl = self._find_floating_potential(self.I_smooth)

        # Step 3 & 4 — dI/dV and plasma potential
        self.dIdV = self._compute_derivative(self.I_smooth)
        V_p = self._find_plasma_potential(self.dIdV, V_fl)

        # Step 5 — Ion saturation current
        poly_ion, I_ion_sat = self._fit_ion_saturation(self.I_smooth, V_fl)

        # Step 6 — Electron temperature
        self.I_electron = self.I_smooth - np.polyval(poly_ion, self.V)
        poly_te, T_e, ln_Ie, V_te = self._fit_electron_temperature(
            self.I_electron, V_fl, V_p
        )

        # Step 7 — Electron saturation current
        poly_esat, I_e_sat = self._fit_electron_saturation(self.I_smooth, V_p)

        return LangmuirResults(
            V_fl=V_fl,
            V_p=V_p,
            T_e=T_e,
            I_ion_sat=I_ion_sat,
            I_e_sat=I_e_sat,
            poly_ion=poly_ion,
            poly_esat=poly_esat,
            poly_te=poly_te,
            ln_Ie=ln_Ie,
            V_te_region=V_te,
        )

    # ================================================================== #
    #  Pipeline steps — private methods
    # ================================================================== #

    def _smooth(self) -> np.ndarray:
        """
        Step 1 — Apply Savitzky-Golay filter.

        S-G filtering fits a low-degree polynomial to successive overlapping
        windows, which reduces random noise while preserving the derivatives
        of the true curve.  This is critical: both the dI/dV peak (V_p) and
        the slope of ln(I_e) (T_e) are sensitive to noise.
        """
        n = len(self.I)
        window = self.savgol_window

        # Clamp window to a valid odd value that doesn't exceed data length
        window = min(window, n if n % 2 == 1 else n - 1)
        window = max(window, self.savgol_polyorder + 2)
        if window % 2 == 0:
            window += 1

        return savgol_filter(self.I, window, self.savgol_polyorder)

    def _find_floating_potential(self, I_s: np.ndarray) -> float:
        """
        Step 2 — Floating potential: V where net current I = 0.

        The floating potential is the probe bias at which the electron
        and ion currents exactly cancel.  Found by linear interpolation
        between the pair of adjacent points that bracket the zero crossing.

        If multiple zero crossings exist (e.g. noise artefacts at very
        negative bias), the crossing closest to the centre of the voltage
        sweep is used — that is where the physical transition occurs.
        """
        sign = np.sign(I_s)
        crossings = np.where(np.diff(sign) != 0)[0]

        if len(crossings) == 0:
            raise AnalysisError(
                "No zero crossing found in I(V). "
                "Extend the sweep range so that both ion saturation and "
                "electron saturation are captured."
            )

        # Prefer the crossing closest to the voltage-range midpoint
        v_mid = 0.5 * (self.V[0] + self.V[-1])
        idx = crossings[np.argmin(np.abs(self.V[crossings] - v_mid))]

        # Linear interpolation between the bracketing points
        V_fl = self.V[idx] - I_s[idx] * (
            (self.V[idx + 1] - self.V[idx]) / (I_s[idx + 1] - I_s[idx])
        )
        return float(V_fl)

    def _compute_derivative(self, I_s: np.ndarray) -> np.ndarray:
        """
        Step 3 — Differential conductance dI/dV.

        Uses numpy's second-order accurate central-difference scheme.
        The conductance curve is useful both for locating V_p (its maximum)
        and as a diagnostic for curve quality.
        """
        return np.gradient(I_s, self.V)

    def _find_plasma_potential(self, dIdV: np.ndarray, V_fl: float) -> float:
        """
        Step 4 — Plasma potential: voltage of maximum in dI/dV.

        Physical reason: at V = V_p the electrostatic barrier for electrons
        vanishes.  The electron collection rate changes most rapidly here,
        producing the conductance peak.

        The search is restricted to V ≥ V_fl to avoid spurious peaks in the
        ion saturation plateau that can arise from noise.
        """
        mask = self.V >= V_fl
        if mask.sum() < 3:
            # Degenerate case: V_fl is near the end of the sweep
            warnings.warn(
                "V_fl is close to the sweep boundary; V_p search uses full range.",
                stacklevel=3,
            )
            mask = np.ones(len(self.V), dtype=bool)

        local_peak_idx = int(np.argmax(dIdV[mask]))
        global_idx = np.where(mask)[0][local_peak_idx]
        return float(self.V[global_idx])

    def _fit_ion_saturation(
        self, I_s: np.ndarray, V_fl: float
    ) -> Tuple[np.ndarray, float]:
        """
        Step 5 — Ion saturation current via linear fit.

        Well below V_fl all electrons are repelled; only ions reach the probe.
        A linear fit accounts for the slight slope caused by sheath expansion
        (probe collection area grows as the sheath thickens with increasing
        negative bias).  The fit is extrapolated to V_fl to obtain the ion
        current contribution at the floating point.
        """
        v_min, v_max = self.ion_fit_range

        # Clip requested range to what is actually in the sweep
        v_min = max(v_min, self.V.min())
        # Do not extend the ion-fit region above V_fl (electron current contaminates it)
        v_max = min(v_max, V_fl - 2.0, self.V.max())
        if v_max <= v_min:
            # Fallback: lowest 20 % of the swept voltage range
            v_max = self.V.min() + 0.2 * (self.V.max() - self.V.min())

        mask = (self.V >= v_min) & (self.V <= v_max)
        if mask.sum() < 3:
            # Last resort: use the 10 most negative voltage points
            mask = np.zeros(len(self.V), dtype=bool)
            mask[: min(10, len(self.V))] = True

        poly_ion = np.polyfit(self.V[mask], I_s[mask], 1)
        I_ion_at_Vfl = float(np.polyval(poly_ion, V_fl))
        return poly_ion, I_ion_at_Vfl

    def _fit_electron_temperature(
        self,
        I_electron: np.ndarray,
        V_fl: float,
        V_p: float,
    ) -> Tuple[np.ndarray, float, np.ndarray, np.ndarray]:
        """
        Step 6 — Electron temperature from ln(I_e) slope.

        In the Maxwellian transition region [V_fl, V_p]:
            I_e(V) = I_e_sat · exp((V − V_p) / T_e)
        ⟹  ln I_e = V / T_e + const

        A linear fit to ln(I_e) vs V gives slope s = 1/T_e [eV⁻¹],
        so T_e = 1/s [eV].

        Only data points where I_e > 10 fA (B2910BL noise floor) are used
        to avoid log(0) artefacts near V_fl.
        """
        mask = (
            (self.V >= V_fl)
            & (self.V <= V_p)
            & (I_electron > self.CURR_NOISE_FLOOR)
        )

        if mask.sum() < 4:
            raise AnalysisError(
                f"Only {mask.sum()} usable data points in the transition region "
                f"[{V_fl:.2f}, {V_p:.2f}] V for T_e fit. "
                "Possible causes: V_fl ≈ V_p (narrow transition), wrong ion fit "
                "range, or insufficient probe bias coverage."
            )

        ln_Ie = np.log(I_electron[mask])
        V_region = self.V[mask]
        poly_te = np.polyfit(V_region, ln_Ie, 1)
        slope = poly_te[0]

        if slope <= 0.0:
            raise AnalysisError(
                f"Non-physical T_e slope {slope:.4g} V⁻¹ (must be > 0). "
                "Check that the ion saturation fit range is in the pure ion "
                "saturation plateau and does not include the transition region."
            )

        T_e = 1.0 / slope   # T_e in eV
        return poly_te, float(T_e), ln_Ie, V_region

    def _fit_electron_saturation(
        self, I_s: np.ndarray, V_p: float
    ) -> Tuple[Optional[np.ndarray], float]:
        """
        Step 7 — Electron saturation current with sheath-expansion correction.

        Above V_p all electrons are collected, but the I(V) curve still rises
        slightly because the probe sheath area increases with voltage (Bohm
        sheath criterion).  A linear fit to I(V) for V > V_p extracts this
        slope, and extrapolating the fit back to V_p removes the sheath-
        expansion artefact to give the 'true' electron saturation current.
        """
        mask = self.V > V_p
        if mask.sum() < 3:
            warnings.warn(
                "Fewer than 3 data points above V_p; I_e_sat taken as I(V_p).",
                stacklevel=3,
            )
            idx_vp = int(np.argmin(np.abs(self.V - V_p)))
            return None, float(I_s[idx_vp])

        poly_esat = np.polyfit(self.V[mask], I_s[mask], 1)
        I_e_sat = float(np.polyval(poly_esat, V_p))
        return poly_esat, I_e_sat
