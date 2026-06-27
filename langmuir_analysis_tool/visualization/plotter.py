"""
Darstellung der Langmuirsonden-Ergebnisse (Analyse-Tool)
=========================================================
Panels:
  1 – I(V)-Kennlinie mit Fits und Markierungen
  2 – ln(I_e) vs V für T_e-Bestimmung
  3 – dI/dV Leitfähigkeit

Zusatzfunktionen:
  stromdichte_plot()  – J(V) = I(V) / Fläche für mehrere Sonden überlagert
  vergleichs_plot()   – mehrere I(V)-Kurven in einem Bild
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from physics.langmuir_analysis import LangmuirAnalyzer, LangmuirResults


FARBE_ROH      = "#6baed6"
FARBE_GLATT    = "#2171b5"
FARBE_ION_FIT  = "#e6550d"
FARBE_ESAT_FIT = "#31a354"
FARBE_VFL      = "#fd8d3c"
FARBE_VP       = "#756bb1"
FARBE_LN_IE    = "#74c476"
FARBE_TE_FIT   = "#e31a1c"

# Farbpalette für Mehrfach-Vergleiche
_VERGLEICH_FARBEN = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
]


class LangmuirPlotter:
    """
    Erzeugt Matplotlib-Abbildungen für Langmuirsonden-Daten.

    Parameter
    ---------
    V : np.ndarray          Spannungsarray (V)
    I : np.ndarray          Roher Stromarray (A)
    analyzer : LangmuirAnalyzer   nach .analyze()
    results  : LangmuirResults    Ergebnis von .analyze()
    label : str             Optionaler Beschriftungstext (Dateiname, Sonden-ID)
    """

    def __init__(
        self,
        V: np.ndarray,
        I: np.ndarray,
        analyzer: LangmuirAnalyzer,
        results: LangmuirResults,
        label: str = "",
    ) -> None:
        self.V   = V
        self.I   = I
        self.az  = analyzer
        self.res = results
        self.label = label

    # ------------------------------------------------------------------ #

    def build(self, figgroesse: tuple = (11, 14)) -> plt.Figure:
        """Dreiteilige Abbildung aufbauen und zurückgeben."""
        fig = plt.figure(figsize=figgroesse)
        fig.patch.set_facecolor("#f8f8f8")

        gs    = gridspec.GridSpec(3, 1, figure=fig, hspace=0.42)
        ax_iv = fig.add_subplot(gs[0])
        ax_ln = fig.add_subplot(gs[1])
        ax_dv = fig.add_subplot(gs[2])

        titel = f"Langmuirsonde — I(V)-Analyse"
        if self.label:
            titel += f"  |  {self.label}"
        fig.suptitle(titel, fontsize=13, fontweight="bold", y=0.98)

        meta = (
            f"Filter: {self.az.filter_method}  |  "
            f"Fenster: {self.az.savgol_window}  |  "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        fig.text(0.5, 0.002, meta, ha="center", va="bottom",
                 fontsize=7.5, color="#666666")

        self._panel_iv(ax_iv)
        self._panel_ln_ie(ax_ln)
        self._panel_didv(ax_dv)

        return fig

    # Alias
    def build_figure(self, **kwargs) -> plt.Figure:
        return self.build(**kwargs)

    def speichern(self, pfad: str | Path, aufloesung: int = 150) -> None:
        fig = None
        try:
            fig = self.build()
            fig.savefig(str(pfad), dpi=aufloesung, bbox_inches="tight",
                        facecolor=fig.get_facecolor())
        finally:
            if fig is not None:
                plt.close(fig)

    def save(self, path: str | Path, dpi: int = 150) -> None:
        self.speichern(path, dpi)

    # ------------------------------------------------------------------ #
    #  Panel 1 – I(V)
    # ------------------------------------------------------------------ #

    def _panel_iv(self, ax: plt.Axes) -> None:
        res     = self.res
        V, I    = self.V, self.I
        I_glatt = self.az.I_smooth

        ax.scatter(V, I * 1e3, s=3, color=FARBE_ROH, alpha=0.45, zorder=1,
                   label="Rohdaten")
        ax.plot(V, I_glatt * 1e3, color=FARBE_GLATT, lw=1.8, zorder=3,
                label="Geglättet")

        V_ion = np.linspace(V.min(), res.V_fl, 300)
        ax.plot(V_ion, np.polyval(res.poly_ion, V_ion) * 1e3,
                color=FARBE_ION_FIT, lw=1.6, ls="--", zorder=4,
                label="Ionensättigungs-Fit")

        if res.poly_esat is not None:
            V_es = np.linspace(res.V_p, V.max(), 300)
            ax.plot(V_es, np.polyval(res.poly_esat, V_es) * 1e3,
                    color=FARBE_ESAT_FIT, lw=1.6, ls="--", zorder=4,
                    label="Elektronensättigungs-Fit")

        ax.axvline(res.V_fl, color=FARBE_VFL, lw=1.2, ls=":",
                   label=f"$V_{{fl}}$ = {res.V_fl:.2f} V")
        ax.axvline(res.V_p, color=FARBE_VP, lw=1.2, ls=":",
                   label=f"$V_p$ = {res.V_p:.2f} V")
        ax.axhline(0, color="black", lw=0.6, zorder=0)
        ax.axhline(res.I_ion_sat * 1e3, color=FARBE_ION_FIT, lw=0.8,
                   ls=":", alpha=0.6,
                   label=r"$I_{\mathrm{ion,sat}}$ = " +
                         f"{res.I_ion_sat * 1e3:.4g} mA")

        txt = (
            f"$V_{{fl}}$ = {res.V_fl:+.2f} V\n"
            f"$V_p$     = {res.V_p:+.2f} V\n"
            f"$T_e$      = {res.T_e:.2f} eV\n"
            f"$I_{{ion,sat}}$ = {res.I_ion_sat * 1e3:.4g} mA\n"
            f"$I_{{e,sat}}$   = {res.I_e_sat * 1e3:.4g} mA"
        )
        rahmen = dict(boxstyle="round,pad=0.5", facecolor="lightyellow",
                      edgecolor="gray", alpha=0.90)
        ax.text(0.02, 0.97, txt, transform=ax.transAxes, fontsize=9.5,
                verticalalignment="top", bbox=rahmen, zorder=10)

        ax.set_xlabel("Sonden-Bias $V$ (V)", fontsize=11)
        ax.set_ylabel("Sondenstrom $I$ (mA)", fontsize=11)
        ax.set_title("Panel 1 — I(V)-Kennlinie", fontsize=11, fontweight="bold")
        ax.legend(loc="lower right", fontsize=8, framealpha=0.85)
        ax.grid(True, alpha=0.25)
        _achsen_formatieren(ax)

    # ------------------------------------------------------------------ #
    #  Panel 2 – ln(I_e)
    # ------------------------------------------------------------------ #

    def _panel_ln_ie(self, ax: plt.Axes) -> None:
        res   = self.res
        V_te  = res.V_te_region
        ln_Ie = res.ln_Ie

        ax.scatter(V_te, ln_Ie, s=8, color=FARBE_LN_IE, alpha=0.7, zorder=2,
                   label=r"$\ln(I_e\,/\,\mathrm{A})$")

        residuen = ln_Ie - np.polyval(res.poly_te, V_te)
        ss_res   = np.sum(residuen ** 2)
        ss_tot   = np.sum((ln_Ie - np.mean(ln_Ie)) ** 2)
        r2       = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

        V_l = np.linspace(V_te.min(), V_te.max(), 300)
        ax.plot(V_l, np.polyval(res.poly_te, V_l),
                color=FARBE_TE_FIT, lw=2.0, zorder=3,
                label=(f"Fit: $T_e$ = {res.T_e:.2f} eV   "
                       f"$R^2$ = {r2:.4f}   N = {len(V_te)}"))

        ax.axvline(res.V_fl, color=FARBE_VFL, lw=1.1, ls=":", alpha=0.8,
                   label=f"$V_{{fl}}$ = {res.V_fl:.2f} V")
        ax.axvline(res.V_p, color=FARBE_VP, lw=1.1, ls=":", alpha=0.8,
                   label=f"$V_p$ = {res.V_p:.2f} V")

        ax.set_xlabel("Sonden-Bias $V$ (V)", fontsize=11)
        ax.set_ylabel(r"$\ln(I_e\,/\,\mathrm{A})$", fontsize=11)
        ax.set_title(
            f"Panel 2 — Elektronentemperatur $T_e$ = {res.T_e:.2f} eV"
            f"   ($R^2$ = {r2:.4f})",
            fontsize=11, fontweight="bold")
        ax.legend(fontsize=8, framealpha=0.85)
        ax.grid(True, alpha=0.25)
        _achsen_formatieren(ax)

    # ------------------------------------------------------------------ #
    #  Panel 3 – dI/dV
    # ------------------------------------------------------------------ #

    def _panel_didv(self, ax: plt.Axes) -> None:
        res       = self.res
        dIdV_mS   = self.az.dIdV * 1e3

        ax.plot(self.V, dIdV_mS, color="#555555", lw=1.4,
                label="dI/dV")
        ax.axhline(0, color="black", lw=0.5, zorder=0)

        idx_vp = int(np.argmin(np.abs(self.V - res.V_p)))
        ax.scatter([res.V_p], [dIdV_mS[idx_vp]], color=FARBE_VP, s=60, zorder=5,
                   label=f"Max → $V_p$ = {res.V_p:.2f} V")

        ax.axvline(res.V_p,  color=FARBE_VP,  lw=1.2, ls=":", alpha=0.8)
        ax.axvline(res.V_fl, color=FARBE_VFL, lw=1.1, ls=":", alpha=0.8,
                   label=f"$V_{{fl}}$ = {res.V_fl:.2f} V")

        ax.set_xlabel("Sonden-Bias $V$ (V)", fontsize=11)
        ax.set_ylabel("dI/dV (mA/V)", fontsize=11)
        ax.set_title(
            f"Panel 3 — Differentielle Leitfähigkeit  (Max → $V_p$ = {res.V_p:.2f} V)",
            fontsize=11, fontweight="bold")
        ax.legend(fontsize=8, framealpha=0.85)
        ax.grid(True, alpha=0.25)
        _achsen_formatieren(ax)


# ====================================================================== #
#  Modul-Funktionen (außerhalb der Klasse)
# ====================================================================== #

def stromdichte_plot(
    datensaetze: list[dict],
    figgroesse: tuple = (10, 6),
) -> plt.Figure:
    """
    J(V)-Stromdichte-Kennlinien für mehrere Sonden in einer Abbildung.

    Parameter
    ----------
    datensaetze : list[dict]
        Jedes Element ist ein dict mit Schlüsseln:
          'V'          : np.ndarray  Spannungsarray (V)
          'I'          : np.ndarray  Stromarray (A)
          'flaeche_m2' : float       Sondenoberfläche in m²
          'label'      : str         Legende-Beschriftung
          'I_smooth'   : np.ndarray  (optional) geglätteter Strom
    figgroesse : tuple

    Rückgabe
    --------
    matplotlib.figure.Figure
    """
    fig, ax = plt.subplots(figsize=figgroesse)
    fig.patch.set_facecolor("#f8f8f8")

    for idx, ds in enumerate(datensaetze):
        farbe  = _VERGLEICH_FARBEN[idx % len(_VERGLEICH_FARBEN)]
        V      = ds["V"]
        I      = ds["I"]
        A      = ds["flaeche_m2"]
        lbl    = ds.get("label", f"Sonde {idx + 1}")
        I_s    = ds.get("I_smooth", None)

        J      = I / A          # A/m²
        J_mAcm2 = J * 0.1       # A/m² → mA/cm²

        ax.scatter(V, J_mAcm2, s=4, color=farbe, alpha=0.35, zorder=1)

        if I_s is not None:
            J_s      = I_s / A
            J_s_plot = J_s * 0.1
            ax.plot(V, J_s_plot, color=farbe, lw=2.0, zorder=3, label=lbl)
        else:
            ax.plot(V, J_mAcm2, color=farbe, lw=1.8, zorder=3, label=lbl)

    ax.axhline(0, color="black", lw=0.7)
    ax.set_xlabel("Sonden-Bias $V$ (V)", fontsize=12)
    ax.set_ylabel("Stromdichte $J$ (mA/cm²)", fontsize=12)
    ax.set_title("Stromdichte-Kennlinien J(V) — Sondenvergleich",
                 fontsize=13, fontweight="bold")
    ax.legend(fontsize=9, framealpha=0.9)
    ax.grid(True, alpha=0.25)
    _achsen_formatieren(ax)

    fig.text(0.5, 0.002,
             f"Erstellt: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             ha="center", fontsize=7.5, color="#666666")
    fig.tight_layout()
    return fig


def vergleichs_plot(
    datensaetze: list[dict],
    figgroesse: tuple = (10, 6),
    einheit: str = "mA",
) -> plt.Figure:
    """
    Mehrere I(V)-Rohdaten-Kurven in einem Bild überlagern.

    Parameter
    ----------
    datensaetze : list[dict]
        Jedes Element hat: 'V', 'I', 'label', optional 'I_smooth'
    einheit : str   "mA" oder "µA"
    """
    fig, ax = plt.subplots(figsize=figgroesse)
    fig.patch.set_facecolor("#f8f8f8")

    faktor = 1e3 if einheit == "mA" else 1e6

    for idx, ds in enumerate(datensaetze):
        farbe = _VERGLEICH_FARBEN[idx % len(_VERGLEICH_FARBEN)]
        V     = ds["V"]
        I     = ds["I"]
        lbl   = ds.get("label", f"Sonde {idx + 1}")
        I_s   = ds.get("I_smooth", None)

        ax.scatter(V, I * faktor, s=4, color=farbe, alpha=0.30, zorder=1)
        kurve = I_s if I_s is not None else I
        ax.plot(V, kurve * faktor, color=farbe, lw=2.0, zorder=3, label=lbl)

    ax.axhline(0, color="black", lw=0.7)
    ax.set_xlabel("Sonden-Bias $V$ (V)", fontsize=12)
    ax.set_ylabel(f"Sondenstrom $I$ ({einheit})", fontsize=12)
    ax.set_title("I(V)-Kennlinien — Vergleich", fontsize=13, fontweight="bold")
    ax.legend(fontsize=9, framealpha=0.9)
    ax.grid(True, alpha=0.25)
    _achsen_formatieren(ax)

    fig.text(0.5, 0.002,
             f"Erstellt: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             ha="center", fontsize=7.5, color="#666666")
    fig.tight_layout()
    return fig


def _achsen_formatieren(ax: plt.Axes) -> None:
    ax.set_facecolor("#fdfdfd")
    for rand in ax.spines.values():
        rand.set_linewidth(0.6)
        rand.set_color("#aaaaaa")
    ax.tick_params(direction="in", length=4, width=0.6)
