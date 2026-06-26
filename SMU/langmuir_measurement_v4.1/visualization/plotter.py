"""
Darstellung der Langmuirsonden-Ergebnisse
==========================================
Erstellt eine dreiteilige Matplotlib-Abbildung:

  Panel 1 – I(V)-Kennlinie
    Rohdaten (Streudiagramm), geglättete Kurve, Ionensättigungs-Fit,
    Elektronensättigungs-Fit, Markierungslinien für V_fl und V_p,
    sowie ein Ergebnisfeld mit allen extrahierten Parametern.

  Panel 2 – Logarithmischer Elektronenstrom ln(I_e) vs V
    Zeigt die Maxwell-Verteilung im Übergangsbereich und den
    linearen Fit, dessen Steigung T_e ergibt.

  Panel 3 – Differentielle Leitfähigkeit dI/dV vs V
    Veranschaulicht wie V_p als Leitfähigkeitsmaximum identifiziert wird.

Alle Panels teilen den gleichen Spannungsbereich auf der x-Achse und
verwenden konsistente Farbcodierung:
  Orange  → Schwebepotential V_fl
  Violett → Plasmapotential V_p
  Rot     → Ionensättigungs-Fit
  Grün    → Elektronensättigungs-Fit
  Blau    → Gemessener Strom
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


# Farbpalette (einheitlich über alle Panels)
FARBE_ROH      = "#6baed6"   # Hellblau  – Rohdaten-Streudiagramm
FARBE_GLATT    = "#2171b5"   # Dunkelblau – geglättete I(V)-Kurve
FARBE_ION_FIT  = "#e6550d"   # Orange-Rot – Ionensättigungs-Fit
FARBE_ESAT_FIT = "#31a354"   # Grün       – Elektronensättigungs-Fit
FARBE_VFL      = "#fd8d3c"   # Orange     – V_fl Markierungslinie
FARBE_VP       = "#756bb1"   # Violett    – V_p Markierungslinie
FARBE_LN_IE    = "#74c476"   # Hellgrün   – ln(I_e) Punkte
FARBE_TE_FIT   = "#e31a1c"   # Rot        – T_e Linearer Fit


class LangmuirPlotter:
    """
    Erzeugt publikationsreife Matplotlib-Abbildungen für Langmuirsonden-Daten.

    Parameter
    ---------
    V : np.ndarray
        Spannungsarray (V).
    I : np.ndarray
        Roher Stromarray (A).
    analyzer : LangmuirAnalyzer
        Analyser-Instanz nach dem Aufruf von .analyze() (enthält
        I_smooth, dIdV, I_electron als Arbeitsarrays).
    results : LangmuirResults
        Ergebnisobjekt das von analyzer.analyze() zurückgegeben wurde.
    """

    def __init__(
        self,
        V: np.ndarray,
        I: np.ndarray,
        analyzer: LangmuirAnalyzer,
        results: LangmuirResults,
    ) -> None:
        self.V   = V
        self.I   = I
        self.az  = analyzer    # Kurzreferenz auf den Analyser (enthält Zwischenarrays)
        self.res = results     # Kurzreferenz auf die Ergebnisse

    # ================================================================== #
    #  Öffentliche Schnittstelle
    # ================================================================== #

    def build(self, figgroesse: tuple = (11, 14)) -> plt.Figure:
        """
        Dreiteilige Abbildung aufbauen und zurückgeben.

        Ruft plt.show() NICHT auf – der Aufrufer entscheidet ob angezeigt
        oder gespeichert werden soll.

        Parameter
        ---------
        figgroesse : (Breite, Höhe) in Zoll

        Rückgabe
        --------
        matplotlib.figure.Figure
        """
        fig = plt.figure(figsize=figgroesse)
        fig.patch.set_facecolor("#f8f8f8")

        # Drei Subplots untereinander mit etwas Abstand
        gs     = gridspec.GridSpec(3, 1, figure=fig, hspace=0.42)
        ax_iv  = fig.add_subplot(gs[0])
        ax_ln  = fig.add_subplot(gs[1])
        ax_dv  = fig.add_subplot(gs[2])

        fig.suptitle(
            "Langmuirsonde  —  I(V)-Kennlinien-Analyse",
            fontsize=14, fontweight="bold", y=0.98,
        )

        # Metadaten-Fußzeile: Filtermethode, Fensterbreite, Zeitstempel
        meta = (
            f"Filter: {self.az.filter_method}  |  "
            f"Fenster: {self.az.savgol_window}  |  "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        fig.text(
            0.5, 0.002, meta,
            ha="center", va="bottom", fontsize=7.5,
            color="#666666", transform=fig.transFigure,
        )

        self._panel_iv(ax_iv)
        self._panel_ln_ie(ax_ln)
        self._panel_didv(ax_dv)

        return fig

    # Alias für Rückwärtskompatibilität (GUI ruft build() auf)
    def build_figure(self, **kwargs) -> plt.Figure:
        return self.build(**kwargs)

    def anzeigen(self) -> None:
        """
        Abbildung aufbauen und anzeigen.
        Hinweis: Nicht aus einem laufenden tk.mainloop() aufrufen –
        stattdessen FigureCanvasTkAgg verwenden (wie in der GUI).
        """
        import matplotlib
        if matplotlib.get_backend().lower().startswith("tk"):
            import warnings
            warnings.warn(
                "anzeigen()/show() sollte nicht aus einem laufenden Tk-Mainloop "
                "aufgerufen werden — stattdessen FigureCanvasTkAgg verwenden.",
                UserWarning, stacklevel=2,
            )
        fig = self.build()
        try:
            plt.show()
        finally:
            plt.close(fig)   # Figure-Leak verhindern

    # Alias für englischen Methodennamen
    def show(self) -> None:
        self.anzeigen()

    def speichern(self, pfad: str | Path, aufloesung: int = 150) -> None:
        """
        Abbildung aufbauen und in eine Datei speichern (kein Fenster öffnen).

        Parameter
        ---------
        pfad : str | Path   Ausgabedatei (Erweiterung bestimmt Format: .png, .pdf, .svg).
        aufloesung : int    DPI-Auflösung; 150 für Bildschirm, 300 für Druck.
                            Bei Vektorgrafiken (.svg/.pdf) wird DPI ignoriert.
        """
        fig = None
        try:
            fig = self.build()
            fig.savefig(str(pfad), dpi=aufloesung, bbox_inches="tight",
                        facecolor=fig.get_facecolor())
        except Exception as fehler:
            print(f"[Plotter] Abbildung konnte nicht gespeichert werden: {fehler}")
        finally:
            if fig is not None:
                plt.close(fig)   # Figure-Leak verhindern auch bei Exception

    # Alias für Rückwärtskompatibilität
    def save(self, path: str | Path, dpi: int = 150) -> None:
        self.speichern(path, dpi)

    # ================================================================== #
    #  Panel 1 – I(V)-Kennlinie
    # ================================================================== #

    def _panel_iv(self, ax: plt.Axes) -> None:
        """Panel 1: Rohdaten, geglättete Kurve, Fits und Markierungslinien."""
        res = self.res
        V, I = self.V, self.I
        I_glatt = self.az.I_smooth

        # Rohdaten als Streudiagramm (kleine Punkte, halbtransparent)
        ax.scatter(V, I * 1e3, s=3, color=FARBE_ROH, alpha=0.45, zorder=1,
                   label="Rohdaten")

        # Geglättete I(V)-Kurve
        ax.plot(V, I_glatt * 1e3, color=FARBE_GLATT, lw=1.8, zorder=3,
                label="Geglättete I(V)")

        # Ionensättigungs-Fit (gestrichelt, verlängert bis V_fl)
        V_ion_linie = np.linspace(V.min(), res.V_fl, 300)
        ax.plot(
            V_ion_linie,
            np.polyval(res.poly_ion, V_ion_linie) * 1e3,
            color=FARBE_ION_FIT, lw=1.6, ls="--", zorder=4,
            label="Ionensättigungs-Fit",
        )

        # Elektronensättigungs-Fit (gestrichelt, ab V_p)
        if res.poly_esat is not None:
            V_esat_linie = np.linspace(res.V_p, V.max(), 300)
            ax.plot(
                V_esat_linie,
                np.polyval(res.poly_esat, V_esat_linie) * 1e3,
                color=FARBE_ESAT_FIT, lw=1.6, ls="--", zorder=4,
                label="Elektronensättigungs-Fit",
            )

        # Vertikale Markierungslinien für V_fl und V_p
        ax.axvline(res.V_fl, color=FARBE_VFL, lw=1.2, ls=":",
                   label=f"$V_{{fl}}$ = {res.V_fl:.2f} V")
        ax.axvline(res.V_p, color=FARBE_VP, lw=1.2, ls=":",
                   label=f"$V_p$ = {res.V_p:.2f} V")
        ax.axhline(0, color="black", lw=0.6, zorder=0)  # Nulllinie

        # Horizontale Hilfslinie für den Ionensättigungsstrom (mit Legenden-Eintrag)
        ax.axhline(
            res.I_ion_sat * 1e3,
            color=FARBE_ION_FIT, lw=0.8, ls=":", alpha=0.6,
            label=r"$I_{\mathrm{ion,sat}}$ = " + f"{res.I_ion_sat * 1e3:.4g} mA",
        )

        # Ergebnisfeld oben links (:.4g statt :.2f – verhindert "0.00 mA" bei µA-Strömen)
        ergebnistext = (
            f"$V_{{fl}}$ = {res.V_fl:+.2f} V\n"
            f"$V_p$     = {res.V_p:+.2f} V\n"
            f"$T_e$      = {res.T_e:.2f} eV\n"
            f"$I_{{ion,sat}}$ = {res.I_ion_sat * 1e3:.4g} mA\n"
            f"$I_{{e,sat}}$  = {res.I_e_sat * 1e3:.4g} mA"
        )
        rahmen = dict(boxstyle="round,pad=0.5", facecolor="lightyellow",
                      edgecolor="gray", alpha=0.90)
        ax.text(0.02, 0.97, ergebnistext, transform=ax.transAxes, fontsize=9.5,
                verticalalignment="top", bbox=rahmen, zorder=10)

        ax.set_xlabel("Sonden-Bias  $V$  (V)", fontsize=11)
        ax.set_ylabel("Sondenstrom  $I$  (mA)", fontsize=11)
        ax.set_title("Panel 1 — I(V)-Kennlinie", fontsize=11, fontweight="bold")
        ax.legend(loc="lower right", fontsize=8, framealpha=0.85)
        ax.grid(True, alpha=0.25)
        self._achsen_formatieren(ax)

    # ================================================================== #
    #  Panel 2 – ln(I_e) vs V (T_e-Bestimmung)
    # ================================================================== #

    def _panel_ln_ie(self, ax: plt.Axes) -> None:
        """Panel 2: Logarithmischer Elektronenstrom und linearer T_e-Fit mit R²."""
        res   = self.res
        V_te  = res.V_te_region
        ln_Ie = res.ln_Ie

        # Datenpunkte im Übergangsbereich
        ax.scatter(V_te, ln_Ie, s=8, color=FARBE_LN_IE, alpha=0.7, zorder=2,
                   label=r"$\ln(I_e\,/\,\mathrm{A})$ – Übergangsbereich")

        # R²-Güte des linearen Fits berechnen
        residuen = ln_Ie - np.polyval(res.poly_te, V_te)
        ss_res   = np.sum(residuen ** 2)
        ss_tot   = np.sum((ln_Ie - np.mean(ln_Ie)) ** 2)
        r2       = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

        # Linearer Fit für T_e mit R² und Punktanzahl im Label
        V_linie = np.linspace(V_te.min(), V_te.max(), 300)
        ax.plot(
            V_linie,
            np.polyval(res.poly_te, V_linie),
            color=FARBE_TE_FIT, lw=2.0, zorder=3,
            label=(
                f"Linearer Fit  (Steigung = {res.poly_te[0]:.3f} eV$^{{-1}}$)\n"
                f"$T_e$ = {res.T_e:.2f} eV   "
                f"$R^2$ = {r2:.4f}   N = {len(V_te)}"
            ),
        )

        # Vertikale Markierungslinien
        ax.axvline(res.V_fl, color=FARBE_VFL, lw=1.1, ls=":", alpha=0.8,
                   label=f"$V_{{fl}}$ = {res.V_fl:.2f} V")
        ax.axvline(res.V_p, color=FARBE_VP, lw=1.1, ls=":", alpha=0.8,
                   label=f"$V_p$ = {res.V_p:.2f} V")

        ax.set_xlabel("Sonden-Bias  $V$  (V)", fontsize=11)
        # Korrekte Dimensionsangabe: ln(I_e/A) statt "dimensionslos"
        ax.set_ylabel(r"$\ln(I_e\,/\,\mathrm{A})$", fontsize=11)
        ax.set_title(
            f"Panel 2 — Elektronentemperatur  $T_e$ = {res.T_e:.2f} eV"
            f"   ($R^2$ = {r2:.4f})",
            fontsize=11, fontweight="bold",
        )
        ax.legend(fontsize=8, framealpha=0.85)
        ax.grid(True, alpha=0.25)
        self._achsen_formatieren(ax)

    # ================================================================== #
    #  Panel 3 – dI/dV Leitfähigkeit (V_p-Bestimmung)
    # ================================================================== #

    def _panel_didv(self, ax: plt.Axes) -> None:
        """Panel 3: Differentielle Leitfähigkeit und V_p-Markierung."""
        res        = self.res
        dIdV_in_mS = self.az.dIdV * 1e3   # Umrechnung: A/V × 1e3 = mA/V (= mS)

        ax.plot(self.V, dIdV_in_mS, color="#555555", lw=1.4,
                label="dI/dV (Leitfähigkeit)")
        ax.axhline(0, color="black", lw=0.5, zorder=0)  # Nulllinie

        # Maximum (Plasmapotential) hervorheben
        idx_vp = int(np.argmin(np.abs(self.V - res.V_p)))
        ax.scatter([res.V_p], [dIdV_in_mS[idx_vp]], color=FARBE_VP, s=60, zorder=5,
                   label=f"Maximum → $V_p$ = {res.V_p:.2f} V")

        # Markierungslinien
        ax.axvline(res.V_p, color=FARBE_VP, lw=1.2, ls=":", alpha=0.8)
        ax.axvline(res.V_fl, color=FARBE_VFL, lw=1.1, ls=":", alpha=0.8,
                   label=f"$V_{{fl}}$ = {res.V_fl:.2f} V")

        ax.set_xlabel("Sonden-Bias  $V$  (V)", fontsize=11)
        # mA/V und mS sind äquivalent (1 mS = 1 mA/V), mA/V ist in der
        # Sondendiagnostik-Literatur gebräuchlicher und expliziter
        ax.set_ylabel("dI/dV  (mA/V)", fontsize=11)
        ax.set_title(
            f"Panel 3 — Differentielle Leitfähigkeit  (Maximum → $V_p$ = {res.V_p:.2f} V)",
            fontsize=11, fontweight="bold",
        )
        ax.legend(fontsize=8, framealpha=0.85)
        ax.grid(True, alpha=0.25)
        self._achsen_formatieren(ax)

    # ================================================================== #
    #  Interne Hilfsmethode
    # ================================================================== #

    @staticmethod
    def _achsen_formatieren(ax: plt.Axes) -> None:
        """Einheitliches, sauberes Erscheinungsbild auf einen Achsen-Objekt anwenden."""
        ax.set_facecolor("#fdfdfd")  # Fast weißer Plotbereich
        for rand in ax.spines.values():
            rand.set_linewidth(0.6)
            rand.set_color("#aaaaaa")
        ax.tick_params(direction="in", length=4, width=0.6)
