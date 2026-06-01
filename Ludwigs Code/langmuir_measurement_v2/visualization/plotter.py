"""
Langmuir Probe Visualisation
=============================
Three-panel Matplotlib figure:

  Panel 1 — I(V) characteristic
    Raw data (scatter), smoothed curve, ion saturation linear fit,
    electron saturation linear fit, vertical markers for V_fl and V_p,
    and a results textbox with all extracted parameters.

  Panel 2 — Logarithmic electron current  ln(I_e) vs V
    Shows the Maxwellian distribution in the transition region and the
    linear fit whose slope gives T_e.

  Panel 3 — Differential conductance  dI/dV vs V
    Demonstrates how V_p is identified as the conductance maximum.

All panels share the x-axis voltage range and use consistent colour coding:
  orange  → floating potential V_fl
  purple  → plasma potential V_p
  red     → ion saturation fit
  green   → electron saturation fit
  blue    → measured current
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch

from physics.langmuir_analysis import LangmuirAnalyzer, LangmuirResults


# ------------------------------------------------------------------ #
#  Colour palette (consistent across all panels)
# ------------------------------------------------------------------ #
C_RAW = "#6baed6"         # light blue  — raw scatter
C_SMOOTH = "#2171b5"      # dark blue   — smoothed I(V)
C_ION_FIT = "#e6550d"     # orange-red  — ion saturation fit line
C_ESAT_FIT = "#31a354"    # green       — electron saturation fit line
C_VFL = "#fd8d3c"         # orange      — V_fl vertical marker
C_VP = "#756bb1"          # purple      — V_p vertical marker
C_LN_IE = "#74c476"       # light green — ln(I_e) points
C_TE_FIT = "#e31a1c"      # red         — T_e linear fit


class LangmuirPlotter:
    """
    Produces publication-quality Matplotlib figures for Langmuir probe data.

    Parameters
    ----------
    V : np.ndarray
        Voltage array (V).
    I : np.ndarray
        Raw current array (A).
    analyzer : LangmuirAnalyzer
        Analyser instance after .analyze() has been called (carries
        I_smooth, dIdV, I_electron as working arrays).
    results : LangmuirResults
        Results object returned by analyzer.analyze().

    Examples
    --------
    >>> plotter = LangmuirPlotter(V, I, analyzer, results)
    >>> plotter.show()                    # interactive window
    >>> plotter.save("sweep_001.png")     # save to file
    """

    def __init__(
        self,
        V: np.ndarray,
        I: np.ndarray,
        analyzer: LangmuirAnalyzer,
        results: LangmuirResults,
    ) -> None:
        self.V = V
        self.I = I
        self.az = analyzer
        self.res = results

    # ================================================================== #
    #  Public
    # ================================================================== #

    def build(self, figsize: tuple = (11, 14)) -> plt.Figure:
        """
        Construct and return the three-panel figure.

        Does not call plt.show() — the caller decides whether to display
        or save.

        Parameters
        ----------
        figsize : (width, height) in inches.

        Returns
        -------
        matplotlib.figure.Figure
        """
        fig = plt.figure(figsize=figsize)
        fig.patch.set_facecolor("#f8f8f8")

        gs = gridspec.GridSpec(3, 1, figure=fig, hspace=0.42)
        ax_iv = fig.add_subplot(gs[0])
        ax_ln = fig.add_subplot(gs[1])
        ax_dv = fig.add_subplot(gs[2])

        fig.suptitle(
            "Langmuir Probe  —  I(V) Characterisation",
            fontsize=14, fontweight="bold", y=0.98,
        )

        self._panel_iv(ax_iv)
        self._panel_ln_ie(ax_ln)
        self._panel_didv(ax_dv)

        return fig

    def show(self) -> None:
        """Build the figure and open an interactive Matplotlib window."""
        fig = self.build()
        plt.show()
        plt.close(fig)

    def save(self, path: str | Path, dpi: int = 150) -> None:
        """
        Build the figure and save to *path* without opening a window.

        Parameters
        ----------
        path : str or Path   Output file path (extension determines format).
        dpi  : int           Resolution; 150 is good for screen, 300 for print.
        """
        # Use a non-interactive backend if no display is available
        try:
            fig = self.build()
            fig.savefig(str(path), dpi=dpi, bbox_inches="tight",
                        facecolor=fig.get_facecolor())
            plt.close(fig)
        except Exception as exc:
            print(f"[Plotter] Could not save figure: {exc}")

    # ================================================================== #
    #  Panel 1 — I(V) characteristic
    # ================================================================== #

    def _panel_iv(self, ax: plt.Axes) -> None:
        res = self.res
        V, I = self.V, self.I
        I_s = self.az.I_smooth

        # ----- Raw data -----
        ax.scatter(V, I * 1e3, s=3, color=C_RAW, alpha=0.45,
                   zorder=1, label="Raw data")

        # ----- Smoothed curve -----
        ax.plot(V, I_s * 1e3, color=C_SMOOTH, lw=1.8,
                zorder=3, label="Smoothed I(V)")

        # ----- Ion saturation linear fit (extended to V_fl) -----
        V_ion_line = np.linspace(V.min(), res.V_fl, 300)
        ax.plot(
            V_ion_line,
            np.polyval(res.poly_ion, V_ion_line) * 1e3,
            color=C_ION_FIT, lw=1.6, ls="--",
            zorder=4, label="Ion sat. fit",
        )

        # ----- Electron saturation linear fit (from V_p onward) -----
        if res.poly_esat is not None:
            V_esat_line = np.linspace(res.V_p, V.max(), 300)
            ax.plot(
                V_esat_line,
                np.polyval(res.poly_esat, V_esat_line) * 1e3,
                color=C_ESAT_FIT, lw=1.6, ls="--",
                zorder=4, label="Electron sat. fit",
            )

        # ----- Vertical markers -----
        ymin, ymax = ax.get_ylim()
        ax.axvline(res.V_fl, color=C_VFL, lw=1.2, ls=":",
                   label=f"$V_{{fl}}$ = {res.V_fl:.2f} V")
        ax.axvline(res.V_p, color=C_VP, lw=1.2, ls=":",
                   label=f"$V_p$ = {res.V_p:.2f} V")
        ax.axhline(0, color="black", lw=0.6, zorder=0)

        # ----- Results textbox -----
        textstr = (
            f"$V_{{fl}}$ = {res.V_fl:+.2f} V\n"
            f"$V_p$     = {res.V_p:+.2f} V\n"
            f"$T_e$      = {res.T_e:.2f} eV\n"
            f"$I_{{ion,sat}}$ = {res.I_ion_sat*1e3:.2f} mA\n"
            f"$I_{{e,sat}}$  = {res.I_e_sat*1e3:.2f} mA"
        )
        bbox_props = dict(boxstyle="round,pad=0.5", facecolor="lightyellow",
                          edgecolor="gray", alpha=0.90)
        ax.text(0.02, 0.97, textstr,
                transform=ax.transAxes, fontsize=9.5,
                verticalalignment="top", bbox=bbox_props, zorder=10)

        # ----- Mark I_ion_sat on y-axis -----
        ax.axhline(res.I_ion_sat * 1e3, color=C_ION_FIT, lw=0.8,
                   ls=":", alpha=0.6)

        ax.set_xlabel("Probe bias voltage  $V$  (V)", fontsize=11)
        ax.set_ylabel("Probe current  $I$  (mA)", fontsize=11)
        ax.set_title("Panel 1 — I(V) Characteristic", fontsize=11, fontweight="bold")
        ax.legend(loc="lower right", fontsize=8, framealpha=0.85)
        ax.grid(True, alpha=0.25)
        self._style_axes(ax)

    # ================================================================== #
    #  Panel 2 — ln(I_e) vs V (T_e determination)
    # ================================================================== #

    def _panel_ln_ie(self, ax: plt.Axes) -> None:
        res = self.res
        V_te = res.V_te_region
        ln_Ie = res.ln_Ie

        # ----- Data points in transition region -----
        ax.scatter(V_te, ln_Ie, s=8, color=C_LN_IE, alpha=0.7,
                   zorder=2, label=r"$\ln(I_e)$ — transition region")

        # ----- T_e linear fit -----
        V_line = np.linspace(V_te.min(), V_te.max(), 300)
        ax.plot(V_line, np.polyval(res.poly_te, V_line),
                color=C_TE_FIT, lw=2.0,
                label=(
                    f"Linear fit  (slope = {res.poly_te[0]:.3f} eV$^{{-1}}$)\n"
                    f"$T_e = 1/\\mathrm{{slope}}$ = {res.T_e:.2f} eV"
                ),
                zorder=3)

        # ----- Vertical markers -----
        ax.axvline(res.V_fl, color=C_VFL, lw=1.1, ls=":",
                   label=f"$V_{{fl}}$ = {res.V_fl:.2f} V", alpha=0.8)
        ax.axvline(res.V_p, color=C_VP, lw=1.1, ls=":",
                   label=f"$V_p$ = {res.V_p:.2f} V", alpha=0.8)

        ax.set_xlabel("Probe bias voltage  $V$  (V)", fontsize=11)
        ax.set_ylabel(r"$\ln(I_e)$  (dimensionless)", fontsize=11)
        ax.set_title(
            f"Panel 2 — Electron Temperature  $T_e$ = {res.T_e:.2f} eV",
            fontsize=11, fontweight="bold",
        )
        ax.legend(fontsize=8, framealpha=0.85)
        ax.grid(True, alpha=0.25)
        self._style_axes(ax)

    # ================================================================== #
    #  Panel 3 — dI/dV conductance (V_p identification)
    # ================================================================== #

    def _panel_didv(self, ax: plt.Axes) -> None:
        res = self.res
        dIdV_mS = self.az.dIdV * 1e3   # convert A/V → mA/V = mS

        ax.plot(self.V, dIdV_mS, color="#555555", lw=1.4,
                label="dI/dV (conductance)")
        ax.axhline(0, color="black", lw=0.5, zorder=0)

        # Highlight the peak (V_p)
        idx_vp = int(np.argmin(np.abs(self.V - res.V_p)))
        ax.scatter([res.V_p], [dIdV_mS[idx_vp]], color=C_VP,
                   s=60, zorder=5, label=f"Peak → $V_p$ = {res.V_p:.2f} V")

        ax.axvline(res.V_p, color=C_VP, lw=1.2, ls=":", alpha=0.8)
        ax.axvline(res.V_fl, color=C_VFL, lw=1.1, ls=":", alpha=0.8,
                   label=f"$V_{{fl}}$ = {res.V_fl:.2f} V")

        ax.set_xlabel("Probe bias voltage  $V$  (V)", fontsize=11)
        ax.set_ylabel("dI/dV  (mS)", fontsize=11)
        ax.set_title(
            f"Panel 3 — Differential Conductance  (peak → $V_p$ = {res.V_p:.2f} V)",
            fontsize=11, fontweight="bold",
        )
        ax.legend(fontsize=8, framealpha=0.85)
        ax.grid(True, alpha=0.25)
        self._style_axes(ax)

    # ================================================================== #
    #  Helper
    # ================================================================== #

    @staticmethod
    def _style_axes(ax: plt.Axes) -> None:
        """Apply a consistent, clean style to a single Axes object."""
        ax.set_facecolor("#fdfdfd")
        for spine in ax.spines.values():
            spine.set_linewidth(0.6)
            spine.set_color("#aaaaaa")
        ax.tick_params(direction="in", length=4, width=0.6)
