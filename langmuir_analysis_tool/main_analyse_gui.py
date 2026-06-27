"""
Langmuir-Analyse-Tool — Standalone GUI
=======================================
Analysiert bereits gemessene CSV-Rohdaten.
Kein Messteil — nur Laden, Filtern, Auswerten, Plotten, Exportieren.

Start:  python main_analyse_gui.py

Unterstützte CSV-Formate
  • Neues Format (Test_LPM_Control_Test.py):
      Zeitstempel_PC, DMM_Zeit_Rel_S, Spannung_Soll_V, Spannung_Ist_V,
      Strom_Ist_A, Strom_SMU_A, Modus
  • Altes Format (langmuir_v4.x):
      spannung_V, strom_A  (Kommentarzeilen mit # werden übersprungen)

Funktionen
  • Einzelne Datei laden und analysieren (alle bisherigen Features)
  • Mehrere Dateien gleichzeitig laden für Vergleichs-Plot
  • Stromdichte J(V) berechnen wenn Sondenoberfläche bekannt
  • Filter wählen: savgol / gaussian / moving / median / butterworth / spike_savgol / none
  • Analyseergebnisse als CSV exportieren
  • Plots als PNG/PDF/SVG speichern
"""

from __future__ import annotations

import os
import sys
import traceback
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

# Eigene Module
from utils.csv_loader import csv_laden, FormatFehler
from physics.langmuir_analysis import (
    LangmuirAnalyzer, LangmuirResults, AnalysisError, FILTER_METHODS
)
from visualization.plotter import LangmuirPlotter, stromdichte_plot, vergleichs_plot


# ── Log-Farbtags ──────────────────────────────────────────────────────────────
_TAG_INFO = "info"
_TAG_OK   = "ok"
_TAG_WARN = "warn"
_TAG_ERR  = "err"
_TAG_KOPF = "kopf"

_HINTERGRUND = "#1e1e1e"
_VORDERGRUND = "#d4d4d4"
_SCHRIFT     = ("Courier New", 9)


# ============================================================================ #
#  Tooltip
# ============================================================================ #

class _Tooltip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget  = widget
        self._text    = text
        self._fenster: tk.Toplevel | None = None
        widget.bind("<Enter>", self._anzeigen)
        widget.bind("<Leave>", self._verbergen)

    def _anzeigen(self, _=None) -> None:
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._fenster = tk.Toplevel(self._widget)
        self._fenster.wm_overrideredirect(True)
        self._fenster.wm_geometry(f"+{x}+{y}")
        tk.Label(self._fenster, text=self._text,
                 background="#ffffe0", relief="solid", borderwidth=1,
                 font=("", 8), justify=tk.LEFT).pack()

    def _verbergen(self, _=None) -> None:
        if self._fenster:
            self._fenster.destroy()
            self._fenster = None


def _tooltip(widget: tk.Widget, text: str) -> _Tooltip:
    return _Tooltip(widget, text)


# ============================================================================ #
#  Datensatz-Container
# ============================================================================ #

class Datensatz:
    """Hält alle Daten und Ergebnisse für eine geladene CSV-Datei."""

    def __init__(
        self,
        pfad: Path,
        V: np.ndarray,
        I: np.ndarray,
        meta: dict,
        strom_quelle: str,
    ) -> None:
        self.pfad         = pfad
        self.V            = V
        self.I            = I
        self.meta         = meta
        self.strom_quelle = strom_quelle
        self.label        = pfad.stem

        # Werden nach der Analyse befüllt
        self.analyzer: Optional[LangmuirAnalyzer] = None
        self.results:  Optional[LangmuirResults]  = None
        self.flaeche_m2: Optional[float]          = None  # für Stromdichte


# ============================================================================ #
#  Haupt-GUI
# ============================================================================ #

class AnalyseGUI(tk.Tk):

    def __init__(self) -> None:
        super().__init__()
        self.title("Langmuir-Analyse-Tool")
        self.geometry("1300x820")
        self.minsize(900, 600)
        self.configure(bg="#f0f0f0")

        # Zustand
        self._datensaetze: list[Datensatz] = []
        self._aktiver_idx: int = -1       # welcher Datensatz gerade angezeigt wird
        self._laeuft = False              # Analyse läuft gerade?

        self._oberflaeche_aufbauen()
        self._log_header()

    # ======================================================================= #
    #  Oberflächenaufbau
    # ======================================================================= #

    def _oberflaeche_aufbauen(self) -> None:
        """Fenster in drei Bereiche aufteilen: Sidebar | Plot | Log."""
        haupt = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        haupt.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # ── linke Sidebar ───────────────────────────────────────────────────
        sidebar_rahmen = ttk.Frame(haupt, width=320)
        sidebar_rahmen.pack_propagate(False)
        haupt.add(sidebar_rahmen, weight=0)

        sidebar = ttk.Frame(sidebar_rahmen)
        sidebar.pack(fill=tk.BOTH, expand=True)

        # ── rechte Seite: Plot oben, Log unten ─────────────────────────────
        rechts = ttk.PanedWindow(haupt, orient=tk.VERTICAL)
        haupt.add(rechts, weight=1)

        plot_rahmen = ttk.Frame(rechts)
        rechts.add(plot_rahmen, weight=3)

        log_rahmen = ttk.Frame(rechts, height=160)
        log_rahmen.pack_propagate(False)
        rechts.add(log_rahmen, weight=1)

        self._sidebar_aufbauen(sidebar)
        self._plot_bereich_aufbauen(plot_rahmen)
        self._log_bereich_aufbauen(log_rahmen)

        # Statusleiste
        self._status_var = tk.StringVar(value="Bereit — Datei laden um zu beginnen")
        status = ttk.Label(self, textvariable=self._status_var,
                           relief=tk.SUNKEN, anchor=tk.W, font=("", 9))
        status.pack(fill=tk.X, side=tk.BOTTOM, padx=0, pady=0)
        self._status_lbl = status

    # ------------------------------------------------------------------ #
    #  Sidebar
    # ------------------------------------------------------------------ #

    def _sidebar_aufbauen(self, elternteil: ttk.Frame) -> None:
        nb = ttk.Notebook(elternteil)
        nb.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._tab_dateien(nb)
        self._tab_analyse(nb)
        self._tab_vergleich(nb)
        self._tab_export(nb)

    # ── Tab 1: Dateien ──────────────────────────────────────────────────
    def _tab_dateien(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Dateien")

        ttk.Label(tab, text="Geladene Dateien", font=("", 9, "bold")).pack(
            anchor="w", padx=8, pady=(8, 2))

        rahmen = ttk.Frame(tab)
        rahmen.pack(fill=tk.BOTH, expand=True, padx=8, pady=2)

        sb = ttk.Scrollbar(rahmen, orient=tk.VERTICAL)
        self._datei_liste = tk.Listbox(
            rahmen, yscrollcommand=sb.set, selectmode=tk.SINGLE,
            font=("", 9), activestyle="dotbox", height=8)
        sb.config(command=self._datei_liste.yview)
        self._datei_liste.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._datei_liste.bind("<<ListboxSelect>>", self._datei_auswaehlen)

        btn_zeile = ttk.Frame(tab)
        btn_zeile.pack(fill=tk.X, padx=8, pady=4)
        self._laden_btn = ttk.Button(btn_zeile, text="+ Laden",
                   command=self._dateien_laden)
        self._laden_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._entfernen_btn = ttk.Button(btn_zeile, text="✕ Entfernen",
                   command=self._datei_entfernen)
        self._entfernen_btn.pack(side=tk.LEFT)

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        # Strom-Quelle wählen
        ttk.Label(tab, text="Strom-Quelle (beim Laden):", font=("", 9)).pack(
            anchor="w", padx=8)
        self._strom_quelle_var = tk.StringVar(value="auto")
        for wert, text in [("auto",     "Automatisch erkennen"),
                            ("smu",      "SMU  (Strom_SMU_A)"),
                            ("keithley", "Keithley  (Strom_Ist_A)")]:
            ttk.Radiobutton(tab, text=text, variable=self._strom_quelle_var,
                            value=wert).pack(anchor="w", padx=20)
        _tooltip(tab,
                 "Automatisch: erkennt den Modus aus dem CSV-Inhalt\n"
                 "SMU: nutzt Strom_SMU_A (Modus 1 — einfache Sonde)\n"
                 "Keithley: nutzt Strom_Ist_A (Modus 2 — Guardring)")

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        # Metadaten der aktiven Datei
        ttk.Label(tab, text="Aktive Datei — Info:", font=("", 9, "bold")).pack(
            anchor="w", padx=8, pady=(0, 2))
        self._meta_var = tk.StringVar(value="—")
        ttk.Label(tab, textvariable=self._meta_var, font=("", 8),
                  foreground="#555555", wraplength=290, justify=tk.LEFT).pack(
            anchor="w", padx=8)

    # ── Tab 2: Analyse-Parameter ────────────────────────────────────────
    def _tab_analyse(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Analyse")

        def zeile(text, standard, tooltip=""):
            f = ttk.Frame(tab)
            f.pack(fill=tk.X, padx=8, pady=3)
            ttk.Label(f, text=text, width=20, anchor="w").pack(side=tk.LEFT)
            var = tk.StringVar(value=standard)
            e   = ttk.Entry(f, textvariable=var, width=10)
            e.pack(side=tk.LEFT)
            if tooltip:
                _tooltip(e, tooltip)
            return var

        ttk.Label(tab, text="Filter & Fenster", font=("", 9, "bold")).pack(
            anchor="w", padx=8, pady=(8, 2))

        # Filtermethode
        f_filter = ttk.Frame(tab)
        f_filter.pack(fill=tk.X, padx=8, pady=3)
        ttk.Label(f_filter, text="Filtermethode:", width=20, anchor="w").pack(side=tk.LEFT)
        self._filter_var = tk.StringVar(value="savgol")
        cb = ttk.Combobox(f_filter, textvariable=self._filter_var,
                          values=list(FILTER_METHODS), state="readonly", width=14)
        cb.pack(side=tk.LEFT)
        _tooltip(cb, "savgol = Standard (bewahrt Kurvenform)\n"
                     "spike_savgol = für verrauschte Labordaten empfohlen")

        self._filter_fenster_var = zeile("Filterfenster:", "21",
            "Ungerade Zahl, z. B. 21\nGrößer = stärker geglättet")
        self._filter_poly_var    = zeile("SavGol Polyordnung:", "3",
            "Polynomgrad für Savitzky-Golay (Standard: 3)")

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(tab, text="Ionensättigungs-Fit Bereich (V)",
                  font=("", 9, "bold")).pack(anchor="w", padx=8, pady=(0, 2))

        self._ion_start_var = zeile("Startspannung:", "-50",
            "Untere Grenze des Ionensättigungs-Fit-Bereichs\n"
            "Muss deutlich unter V_fl liegen")
        self._ion_stop_var  = zeile("Stoppspannung:", "-30",
            "Obere Grenze — wird automatisch auf V_fl−2V begrenzt")

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        # Analyse starten
        self._analyse_btn = ttk.Button(
            tab, text="▶  Analyse starten",
            command=self._analyse_starten)
        self._analyse_btn.pack(fill=tk.X, padx=8, pady=4)
        _tooltip(self._analyse_btn, "Analysiert die aktuell gewählte Datei")

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=4)

        # Ergebnisse-Textbox
        ttk.Label(tab, text="Ergebnisse:", font=("", 9, "bold")).pack(
            anchor="w", padx=8)
        self._ergebnis_var = tk.StringVar(value="—")
        ttk.Label(tab, textvariable=self._ergebnis_var, font=("Courier New", 8),
                  foreground="#003366", justify=tk.LEFT, wraplength=285).pack(
            anchor="w", padx=8, pady=2)

    # ── Tab 3: Vergleich / Stromdichte ──────────────────────────────────
    def _tab_vergleich(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Vergleich")

        ttk.Label(tab, text="Alle geladenen Dateien vergleichen",
                  font=("", 9, "bold")).pack(anchor="w", padx=8, pady=(8, 4))

        # I(V)-Vergleich
        f_einheit = ttk.Frame(tab)
        f_einheit.pack(fill=tk.X, padx=8, pady=2)
        ttk.Label(f_einheit, text="Einheit:", width=14, anchor="w").pack(side=tk.LEFT)
        self._einheit_var = tk.StringVar(value="mA")
        ttk.Radiobutton(f_einheit, text="mA", variable=self._einheit_var,
                        value="mA").pack(side=tk.LEFT)
        ttk.Radiobutton(f_einheit, text="µA", variable=self._einheit_var,
                        value="µA").pack(side=tk.LEFT, padx=8)

        ttk.Button(tab, text="I(V)-Vergleichs-Plot",
                   command=self._vergleichs_plot_zeigen).pack(
            fill=tk.X, padx=8, pady=4)

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=8)

        # Stromdichte
        ttk.Label(tab, text="Stromdichte J(V) = I / Fläche",
                  font=("", 9, "bold")).pack(anchor="w", padx=8, pady=(0, 4))
        ttk.Label(tab, text="Oberflächen in mm² (eine pro Datei,\ndurch Semikolon getrennt):",
                  font=("", 8), foreground="#555").pack(anchor="w", padx=8)

        self._flaechen_var = tk.StringVar(value="")
        e_fl = ttk.Entry(tab, textvariable=self._flaechen_var)
        e_fl.pack(fill=tk.X, padx=8, pady=2)
        _tooltip(e_fl,
                 "Oberflächen der Sonden in mm², durch Semikolon getrennt.\n"
                 "Dezimaltrenner: Punkt oder Komma möglich.\n"
                 "Reihenfolge entspricht der Datei-Liste.\n"
                 "Beispiel:  12,5; 12,5; 25,0")

        ttk.Button(tab, text="J(V)-Stromdichte-Plot",
                   command=self._stromdichte_plot_zeigen).pack(
            fill=tk.X, padx=8, pady=4)

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        ttk.Label(tab, text="Hinweis: Für Vergleichs- und Stromdichte-Plots\n"
                             "müssen alle Dateien zuerst analysiert werden.",
                  font=("", 8), foreground="#888").pack(anchor="w", padx=8)

    # ── Tab 4: Export ────────────────────────────────────────────────────
    def _tab_export(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Export")

        ttk.Label(tab, text="Ausgabe-Ordner:", font=("", 9)).pack(
            anchor="w", padx=8, pady=(8, 2))

        f_ordner = ttk.Frame(tab)
        f_ordner.pack(fill=tk.X, padx=8, pady=2)
        self._ausgabe_ordner_var = tk.StringVar(value="analyse_ergebnisse")
        ttk.Entry(f_ordner, textvariable=self._ausgabe_ordner_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(f_ordner, text="…", width=3,
                   command=self._ordner_auswaehlen).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=8)

        ttk.Label(tab, text="Plot speichern", font=("", 9, "bold")).pack(
            anchor="w", padx=8)

        f_format = ttk.Frame(tab)
        f_format.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(f_format, text="Format:", width=10, anchor="w").pack(side=tk.LEFT)
        self._plot_format_var = tk.StringVar(value="png")
        for fmt in ("png", "pdf", "svg"):
            ttk.Radiobutton(f_format, text=fmt.upper(),
                            variable=self._plot_format_var, value=fmt).pack(
                side=tk.LEFT, padx=4)

        f_dpi = ttk.Frame(tab)
        f_dpi.pack(fill=tk.X, padx=8, pady=2)
        ttk.Label(f_dpi, text="DPI:", width=10, anchor="w").pack(side=tk.LEFT)
        self._dpi_var = tk.StringVar(value="150")
        ttk.Entry(f_dpi, textvariable=self._dpi_var, width=6).pack(side=tk.LEFT)

        ttk.Button(tab, text="Aktuellen Plot speichern",
                   command=self._plot_speichern).pack(fill=tk.X, padx=8, pady=4)

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        ttk.Label(tab, text="Analyseergebnisse exportieren",
                  font=("", 9, "bold")).pack(anchor="w", padx=8)
        ttk.Button(tab, text="Ergebnisse als CSV speichern",
                   command=self._ergebnisse_speichern).pack(
            fill=tk.X, padx=8, pady=4)
        ttk.Button(tab, text="Alle Ergebnisse (Batch) speichern",
                   command=self._alle_ergebnisse_speichern).pack(
            fill=tk.X, padx=8, pady=2)

    # ------------------------------------------------------------------ #
    #  Plot-Bereich
    # ------------------------------------------------------------------ #

    def _plot_bereich_aufbauen(self, elternteil: ttk.Frame) -> None:
        """Leerer Plot-Bereich mit NavigationToolbar."""
        self._plot_container = elternteil   # für späteres Canvas-Ersetzen merken

        self._fig = plt.figure(figsize=(8, 9))
        self._fig.patch.set_facecolor("#f8f8f8")
        ax = self._fig.add_subplot(1, 1, 1)
        ax.text(0.5, 0.5, "Datei laden und Analyse starten",
                ha="center", va="center", fontsize=13, color="#aaaaaa",
                transform=ax.transAxes)
        ax.axis("off")

        self._canvas = FigureCanvasTkAgg(self._fig, master=elternteil)
        self._canvas.draw()
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self._toolbar_rahmen = ttk.Frame(elternteil)
        self._toolbar_rahmen.pack(fill=tk.X)
        self._toolbar = NavigationToolbar2Tk(self._canvas, self._toolbar_rahmen)
        self._toolbar.update()

    # ------------------------------------------------------------------ #
    #  Log-Bereich
    # ------------------------------------------------------------------ #

    def _log_bereich_aufbauen(self, elternteil: ttk.Frame) -> None:
        from tkinter.scrolledtext import ScrolledText
        ttk.Label(elternteil, text="Protokoll", font=("", 8, "bold")).pack(
            anchor="w", padx=4)
        self._log_feld = ScrolledText(
            elternteil,
            font=_SCHRIFT, bg=_HINTERGRUND, fg=_VORDERGRUND,
            insertbackground=_VORDERGRUND,
            state="disabled", height=6, wrap=tk.WORD,
        )
        self._log_feld.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        self._log_feld.tag_config(_TAG_INFO, foreground="#aaaaaa")
        self._log_feld.tag_config(_TAG_OK,   foreground="#6fcf97")
        self._log_feld.tag_config(_TAG_WARN, foreground="#f2c94c")
        self._log_feld.tag_config(_TAG_ERR,  foreground="#eb5757")
        self._log_feld.tag_config(_TAG_KOPF, foreground="#56ccf2")

    # ======================================================================= #
    #  Logging
    # ======================================================================= #

    def _log(self, text: str, tag: str = _TAG_INFO) -> None:
        def _anhaengen() -> None:
            self._log_feld.configure(state="normal")
            self._log_feld.insert(tk.END, text, tag)
            self._log_feld.see(tk.END)
            self._log_feld.configure(state="disabled")
        self.after(0, _anhaengen)

    def _status(self, text: str, farbe: str = "") -> None:
        def _setzen() -> None:
            self._status_var.set(text)
            self._status_lbl.configure(foreground=farbe if farbe else "")
        self.after(0, _setzen)

    def _log_header(self) -> None:
        self._log("=" * 55 + "\n", _TAG_KOPF)
        self._log("  Langmuir-Analyse-Tool\n", _TAG_KOPF)
        self._log("  Kein Messteil — nur Rohdaten-Auswertung\n", _TAG_KOPF)
        self._log("=" * 55 + "\n", _TAG_KOPF)
        self._log("Dateien laden → Analyse starten\n", _TAG_INFO)

    # ======================================================================= #
    #  Dateiverwaltung
    # ======================================================================= #

    def _dateien_laden(self) -> None:
        pfade = filedialog.askopenfilenames(
            title="CSV-Rohdateien öffnen",
            filetypes=[("CSV-Dateien", "*.csv"), ("Alle Dateien", "*.*")],
        )
        if not pfade:
            return

        strom_quelle = self._strom_quelle_var.get()

        for pfad_str in pfade:
            pfad = Path(pfad_str)
            # Doppelt laden verhindern
            if any(ds.pfad == pfad for ds in self._datensaetze):
                self._log(f"[SKIP] Bereits geladen: {pfad.name}\n", _TAG_WARN)
                continue
            try:
                V, I, meta = csv_laden(pfad, strom_quelle=strom_quelle)
                ds = Datensatz(pfad, V, I, meta, meta["strom_quelle"])
                self._datensaetze.append(ds)
                self._datei_liste.insert(tk.END, pfad.name)
                self._log(
                    f"[OK] {pfad.name}  ({meta['n_punkte']} Punkte, "
                    f"Format: {meta['format']}, Strom: {meta['strom_quelle']}"
                    + (f", {meta['punkte_verworfen']} verworfen" if meta.get('punkte_verworfen', 0) > 0 else "")
                    + ")\n",
                    _TAG_OK,
                )
            except (FormatFehler, FileNotFoundError, Exception) as e:
                self._log(f"[FEHLER] {pfad.name}: {e}\n", _TAG_ERR)

        if self._datensaetze:
            self._datei_liste.selection_clear(0, tk.END)
            self._datei_liste.selection_set(len(self._datensaetze) - 1)
            self._datei_auswaehlen()

    def _datei_auswaehlen(self, _event=None) -> None:
        auswahl = self._datei_liste.curselection()
        if not auswahl:
            return
        idx = auswahl[0]
        if idx >= len(self._datensaetze):
            return
        self._aktiver_idx = idx
        ds = self._datensaetze[idx]

        # Meta-Info aktualisieren
        verworfen = ds.meta.get("punkte_verworfen", 0)
        info = (
            f"Datei:  {ds.pfad.name}\n"
            f"Punkte: {ds.meta['n_punkte']}"
            + (f"  ({verworfen} verworfen)" if verworfen > 0 else "")
            + f"\nFormat: {ds.meta['format']}\n"
            f"Modus:  {ds.meta.get('modus', '?')}\n"
            f"Strom:  {ds.strom_quelle}\n"
            f"V: [{ds.V.min():.2f} … {ds.V.max():.2f}] V"
        )
        self._meta_var.set(info)

        # Wenn Ergebnisse vorhanden → Ergebnistext setzen und Plot aktualisieren
        if ds.results is not None:
            self._ergebnis_var.set(ds.results.zusammenfassung())
            self._plot_aktualisieren(ds)
        else:
            self._ergebnis_var.set("Noch nicht analysiert.")

    def _datei_entfernen(self) -> None:
        if self._laeuft:
            return
        auswahl = self._datei_liste.curselection()
        if not auswahl:
            return
        idx  = auswahl[0]
        name = self._datensaetze[idx].pfad.name
        if not messagebox.askyesno("Entfernen", f"'{name}' aus der Liste entfernen?"):
            return
        self._datei_liste.delete(idx)
        del self._datensaetze[idx]
        self._aktiver_idx = -1
        self._ergebnis_var.set("—")
        self._meta_var.set("—")

    # ======================================================================= #
    #  Analyse
    # ======================================================================= #

    def _analyse_starten(self) -> None:
        if self._laeuft:
            return
        if self._aktiver_idx < 0 or self._aktiver_idx >= len(self._datensaetze):
            messagebox.showwarning("Keine Datei", "Bitte zuerst eine Datei auswählen.")
            return

        # Parameter lesen
        try:
            filter_methode = self._filter_var.get()
            fenster        = int(self._filter_fenster_var.get())
            poly           = int(self._filter_poly_var.get())
            ion_start      = float(self._ion_start_var.get())
            ion_stop       = float(self._ion_stop_var.get())
        except ValueError as e:
            messagebox.showerror("Ungültige Parameter", str(e))
            return

        self._laeuft = True
        self._analyse_btn.configure(state="disabled")
        self._laden_btn.configure(state="disabled")
        self._entfernen_btn.configure(state="disabled")
        self._status("Analyse läuft …")

        threading.Thread(
            target=self._analyse_thread,
            args=(self._aktiver_idx, filter_methode, fenster, poly,
                  ion_start, ion_stop),
            daemon=True,
        ).start()

    def _analyse_thread(
        self,
        idx: int,
        filter_methode: str,
        fenster: int,
        poly: int,
        ion_start: float,
        ion_stop: float,
    ) -> None:
        ds = self._datensaetze[idx]
        self._log(f"\n[Analyse] {ds.pfad.name}\n", _TAG_KOPF)
        self._log(f"  Filter: {filter_methode}  Fenster: {fenster}\n", _TAG_INFO)
        self._log(f"  Ionenfit: [{ion_start:.1f}, {ion_stop:.1f}] V\n", _TAG_INFO)

        try:
            az = LangmuirAnalyzer(
                ds.V, ds.I,
                savgol_window=fenster,
                savgol_polyorder=poly,
                ion_fit_range=(ion_start, ion_stop),
                filter_method=filter_methode,
            )
            ergebnisse = az.analyze()
            ds.analyzer = az
            ds.results  = ergebnisse

            self._log(ergebnisse.zusammenfassung() + "\n", _TAG_OK)
            self.after(0, lambda: self._ergebnis_var.set(ergebnisse.zusammenfassung()))
            self.after(0, lambda: self._plot_aktualisieren(ds))
            self._status(f"Analyse abgeschlossen — {ds.pfad.name}")

        except AnalysisError as e:
            self._log(f"[ANALYSE-FEHLER] {e}\n", _TAG_ERR)
            self._status("Analyse-Fehler — Details im Protokoll", "red")
        except Exception:
            tb = traceback.format_exc()
            self._log(f"[FEHLER]\n{tb}\n", _TAG_ERR)
            self._status("Unerwarteter Fehler", "red")
        finally:
            self._laeuft = False
            self.after(0, lambda: self._analyse_btn.configure(state="normal"))
            self.after(0, lambda: self._laden_btn.configure(state="normal"))
            self.after(0, lambda: self._entfernen_btn.configure(state="normal"))

    # ======================================================================= #
    #  Plot
    # ======================================================================= #

    def _plot_aktualisieren(self, ds: Datensatz) -> None:
        """Analyse-Plot für einen Datensatz in den Canvas zeichnen (kein Memory-Leak)."""
        if ds.analyzer is None or ds.results is None:
            return

        plt.close(self._fig)

        plotter = LangmuirPlotter(ds.V, ds.I, ds.analyzer, ds.results, label=ds.label)
        self._fig = plotter.build(figgroesse=(8, 9))

        # Canvas und Toolbar komplett ersetzen — sicherer als figure-Zuweisung
        try:
            self._canvas.get_tk_widget().destroy()
            self._toolbar.destroy()
            self._toolbar_rahmen.destroy()
        except Exception:
            pass

        self._canvas = FigureCanvasTkAgg(self._fig, master=self._plot_container)
        self._canvas.draw()
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self._toolbar_rahmen = ttk.Frame(self._plot_container)
        self._toolbar_rahmen.pack(fill=tk.X)
        self._toolbar = NavigationToolbar2Tk(self._canvas, self._toolbar_rahmen)
        self._toolbar.update()

    def _vergleichs_plot_zeigen(self) -> None:
        if len(self._datensaetze) < 1:
            messagebox.showinfo("Keine Daten", "Bitte erst Dateien laden.")
            return

        analysiert = sum(1 for ds in self._datensaetze if ds.analyzer is not None)
        if analysiert < len(self._datensaetze):
            self._log(
                f"[HINWEIS] {len(self._datensaetze) - analysiert} Datei(en) noch nicht analysiert "
                f"— werden ohne Glättung dargestellt.\n", _TAG_WARN)

        datensaetze_plot = []
        for ds in self._datensaetze:
            eintrag = {
                "V":     ds.V,
                "I":     ds.I,
                "label": ds.label,
            }
            if ds.analyzer is not None:
                eintrag["I_smooth"] = ds.analyzer.I_smooth
            datensaetze_plot.append(eintrag)

        einheit = self._einheit_var.get()
        fig = vergleichs_plot(datensaetze_plot, figgroesse=(9, 6), einheit=einheit)
        self._in_neuem_fenster_zeigen(fig, "I(V)-Vergleich")

    def _stromdichte_plot_zeigen(self) -> None:
        if len(self._datensaetze) < 1:
            messagebox.showinfo("Keine Daten", "Bitte erst Dateien laden.")
            return

        analysiert = sum(1 for ds in self._datensaetze if ds.analyzer is not None)
        if analysiert < len(self._datensaetze):
            self._log(
                f"[HINWEIS] {len(self._datensaetze) - analysiert} Datei(en) noch nicht analysiert "
                f"— werden ohne Glättung dargestellt.\n", _TAG_WARN)

        # Flächen parsen
        flaechen_text = self._flaechen_var.get().strip()
        if not flaechen_text:
            messagebox.showerror(
                "Oberfläche fehlt",
                "Bitte Sondenoberflächen in mm² eingeben (kommagetrennt).")
            return

        try:
            # Semikolon als Listentrenner, Komma als Dezimaltrenner (deutsch)
            flaechen_mm2 = [float(x.strip().replace(",", ".")) for x in flaechen_text.split(";")]
        except ValueError:
            messagebox.showerror("Ungültige Eingabe",
                                 "Oberflächen müssen Zahlen sein, durch Semikolon getrennt.\n"
                                 "Beispiel:  12,5; 12,5; 25,0")
            return

        if any(fl <= 0 for fl in flaechen_mm2):
            messagebox.showerror("Ungültige Fläche",
                                 "Alle Sondenoberflächen müssen > 0 mm² sein.")
            return

        if len(flaechen_mm2) < len(self._datensaetze):
            # Fehlende Werte mit dem letzten auffüllen
            letzter = flaechen_mm2[-1]
            while len(flaechen_mm2) < len(self._datensaetze):
                flaechen_mm2.append(letzter)

        datensaetze_plot = []
        for ds, fl_mm2 in zip(self._datensaetze, flaechen_mm2):
            fl_m2 = fl_mm2 * 1e-6   # mm² → m²
            ds.flaeche_m2 = fl_m2
            eintrag = {
                "V":          ds.V,
                "I":          ds.I,
                "flaeche_m2": fl_m2,
                "label":      f"{ds.label}  ({fl_mm2:.2g} mm²)",
            }
            if ds.analyzer is not None:
                eintrag["I_smooth"] = ds.analyzer.I_smooth
            datensaetze_plot.append(eintrag)

        fig = stromdichte_plot(datensaetze_plot, figgroesse=(9, 6))
        self._in_neuem_fenster_zeigen(fig, "J(V)-Stromdichte")

    def _in_neuem_fenster_zeigen(self, fig: plt.Figure, titel: str) -> None:
        """Matplotlib-Figure in einem neuen Tk-Toplevel-Fenster anzeigen."""
        fenster = tk.Toplevel(self)
        fenster.title(titel)
        fenster.geometry("950x620")

        canvas = FigureCanvasTkAgg(fig, master=fenster)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar_rahmen = ttk.Frame(fenster)
        toolbar_rahmen.pack(fill=tk.X)
        NavigationToolbar2Tk(canvas, toolbar_rahmen).update()

        fenster.protocol("WM_DELETE_WINDOW", lambda: (plt.close(fig), fenster.destroy()))

    # ======================================================================= #
    #  Export
    # ======================================================================= #

    def _ordner_auswaehlen(self) -> None:
        ordner = filedialog.askdirectory(title="Ausgabe-Ordner wählen")
        if ordner:
            self._ausgabe_ordner_var.set(ordner)

    def _plot_speichern(self) -> None:
        if self._aktiver_idx < 0:
            messagebox.showwarning("Keine Datei", "Kein Datensatz aktiv.")
            return
        ds = self._datensaetze[self._aktiver_idx]
        if ds.analyzer is None:
            messagebox.showwarning("Nicht analysiert",
                                   "Erst Analyse starten.")
            return

        fmt    = self._plot_format_var.get()
        ordner = Path(self._ausgabe_ordner_var.get())
        ordner.mkdir(parents=True, exist_ok=True)
        dateiname = ordner / f"langmuir_analyse_{ds.pfad.stem}.{fmt}"

        try:
            dpi = int(self._dpi_var.get())
        except ValueError:
            dpi = 150

        plotter = LangmuirPlotter(ds.V, ds.I, ds.analyzer, ds.results, label=ds.label)
        plotter.speichern(dateiname, aufloesung=dpi)
        self._log(f"[Export] Plot gespeichert: {dateiname}\n", _TAG_OK)
        self._status(f"Plot gespeichert: {dateiname.name}")

    def _ergebnisse_speichern(self) -> None:
        if self._aktiver_idx < 0:
            messagebox.showwarning("Keine Datei", "Kein Datensatz aktiv.")
            return
        ds = self._datensaetze[self._aktiver_idx]
        if ds.results is None:
            messagebox.showwarning("Nicht analysiert", "Erst Analyse starten.")
            return
        self._ergebnisse_als_csv(ds)

    def _alle_ergebnisse_speichern(self) -> None:
        analysierten = [ds for ds in self._datensaetze if ds.results is not None]
        if not analysierten:
            messagebox.showinfo("Nichts zu exportieren",
                                "Noch keine Datei analysiert.")
            return
        for ds in analysierten:
            self._ergebnisse_als_csv(ds)
        self._status(f"{len(analysierten)} Ergebnis-CSV(s) gespeichert.")

    def _ergebnisse_als_csv(self, ds: Datensatz) -> None:
        import csv as _csv
        from datetime import datetime as _dt

        ordner = Path(self._ausgabe_ordner_var.get())
        ordner.mkdir(parents=True, exist_ok=True)
        pfad   = ordner / f"ergebnisse_{ds.pfad.stem}.csv"
        r      = ds.results

        zeilen = [
            ("V_fl_V",      r.V_fl,          "V",   "Schwebepotential"),
            ("V_p_V",       r.V_p,           "V",   "Plasmapotential"),
            ("T_e_eV",      r.T_e,           "eV",  "Elektronentemperatur"),
            ("I_ion_sat_A", r.I_ion_sat,     "A",   "Ionensättigungsstrom"),
            ("I_e_sat_A",   r.I_e_sat,       "A",   "Elektronensättigungsstrom"),
            ("I_ion_sat_mA",r.I_ion_sat*1e3, "mA",  "Ionensättigungsstrom"),
            ("I_e_sat_mA",  r.I_e_sat*1e3,   "mA",  "Elektronensättigungsstrom"),
            ("ion_fit_m",   r.poly_ion[0],   "A/V", "Ionensättigungs-Fit Steigung"),
            ("ion_fit_b",   r.poly_ion[1],   "A",   "Ionensättigungs-Fit Achsenabschnitt"),
            ("esat_fit_m",  r.poly_esat[0] if r.poly_esat is not None else float("nan"), "A/V", "Elektronensättigungs-Fit Steigung"),
            ("esat_fit_b",  r.poly_esat[1] if r.poly_esat is not None else float("nan"), "A",   "Elektronensättigungs-Fit Achsenabschnitt"),
            ("Te_fit_m",    r.poly_te[0],    "1/eV","ln(Ie)-Fit Steigung"),
            ("Te_fit_b",    r.poly_te[1],    "",    "ln(Ie)-Fit Achsenabschnitt"),
        ]

        with open(pfad, "w", newline="", encoding="utf-8") as f:
            f.write(f"# Langmuir Analyseergebnisse — {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Quelldatei: {ds.pfad.name}\n")
            f.write(f"# Strom-Quelle: {ds.strom_quelle}\n")
            writer = _csv.writer(f)
            writer.writerow(["parameter", "wert", "einheit", "beschreibung"])
            for name, wert, einheit, beschr in zeilen:
                writer.writerow([name, f"{wert:.8g}", einheit, beschr])

        self._log(f"[Export] Ergebnisse: {pfad}\n", _TAG_OK)


# ============================================================================ #
#  Einstiegspunkt
# ============================================================================ #

if __name__ == "__main__":
    app = AnalyseGUI()
    app.mainloop()
