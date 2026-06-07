"""
Langmuir Probe Framework  v2  —  GUI  (improved)
=================================================
Start via double-click on  'Start Langmuir GUI.bat'
or directly:  python main_v2_gui.py

Improvements over v1:
  • VISA device scanner with refresh button
  • Dropdown for filter method (Savitzky-Golay / Gaussian / Moving-Avg / Median / None)
  • Progress bar during sweep
  • Status bar with colour coding
  • Dark-mode log window with colour-tagged lines
  • Tooltips on all key controls
  • Resizable paned layout with better proportions
"""

from __future__ import annotations

# ── Backend must be set before any pyplot import ───────────────────────────
import matplotlib
matplotlib.use("TkAgg")

import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

# ── Make shared framework modules importable ───────────────────────────────
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from hardware.connection_manager import ConnectionManager
from hardware.b2910bl_driver import B2910BLDriver
from physics.langmuir_analysis import LangmuirAnalyzer, AnalysisError, FILTER_METHODS
from visualization.plotter import LangmuirPlotter
from utils.data_export import save_raw_data, save_results

# ── Optional pyvisa for VISA scanning ──────────────────────────────────────
try:
    import pyvisa as _pyvisa        # type: ignore
    _PYVISA_OK = True
except ImportError:
    _PYVISA_OK = False

# ── Colour scheme ──────────────────────────────────────────────────────────
_BG   = "#1e1e1e"
_FG   = "#d4d4d4"
_SEL  = "#264f78"
_FONT = ("Courier New", 9)

_TAG_INFO  = "info"
_TAG_OK    = "ok"
_TAG_WARN  = "warn"
_TAG_ERR   = "err"
_TAG_HEAD  = "head"


# ============================================================================ #
#  Tooltip helper
# ============================================================================ #

class _ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text   = text
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _=None) -> None:
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tip, text=self._text,
            background="#ffffe0", relief="solid", borderwidth=1,
            font=("TkDefaultFont", 8), wraplength=300, justify="left",
        ).pack(ipadx=4, ipady=2)

    def _hide(self, _=None) -> None:
        if self._tip:
            self._tip.destroy()
            self._tip = None


def _tip(widget: tk.Widget, text: str) -> None:
    _ToolTip(widget, text)


# ============================================================================ #
#  Main application window
# ============================================================================ #

class LangmuirGUI(tk.Tk):

    def __init__(self) -> None:
        super().__init__()
        self.title("Langmuir Probe Framework  v2")
        self.geometry("1100x720")
        self.minsize(860, 580)
        self.resizable(True, True)

        # ── Runtime state ────────────────────────────────────────────────
        self._running    = False
        self._plotter: LangmuirPlotter | None = None
        self._plot_win: tk.Toplevel | None    = None

        self._build_ui()
        self._log("Bereit.  Einstellungen anpassen und '▶  Messung starten' klicken.\n",
                  _TAG_INFO)

    # ======================================================================= #
    #  UI construction
    # ======================================================================= #

    def _build_ui(self) -> None:
        # ── Top-level layout: settings left | log right ──────────────────
        pw = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))

        # Left column — settings notebook
        left = ttk.Frame(pw, width=340)
        left.pack_propagate(False)
        pw.add(left, weight=0)

        nb = ttk.Notebook(left)
        nb.pack(fill=tk.BOTH, expand=True)
        self._build_tab_connection(nb)
        self._build_tab_sweep(nb)
        self._build_tab_instrument(nb)
        self._build_tab_analysis(nb)
        self._build_tab_output(nb)

        # Right column — log + buttons
        right = ttk.Frame(pw)
        pw.add(right, weight=1)

        ttk.Label(right, text="Log-Ausgabe", font=("", 10, "bold")).pack(anchor="w", pady=(0, 2))

        self._log_box = scrolledtext.ScrolledText(
            right,
            state="disabled",
            wrap=tk.WORD,
            font=_FONT,
            background=_BG,
            foreground=_FG,
            insertbackground="white",
            selectbackground=_SEL,
        )
        # colour tags
        self._log_box.tag_config(_TAG_INFO, foreground="#d4d4d4")
        self._log_box.tag_config(_TAG_OK,   foreground="#4ec9b0")
        self._log_box.tag_config(_TAG_WARN, foreground="#dcdcaa")
        self._log_box.tag_config(_TAG_ERR,  foreground="#f44747")
        self._log_box.tag_config(_TAG_HEAD, foreground="#9cdcfe", font=(_FONT[0], _FONT[1], "bold"))
        self._log_box.pack(fill=tk.BOTH, expand=True)

        # ── Progress bar ─────────────────────────────────────────────────
        self._progress = ttk.Progressbar(right, mode="indeterminate", length=200)
        self._progress.pack(fill=tk.X, pady=(4, 0))

        # ── Button row ───────────────────────────────────────────────────
        btn_frame = ttk.Frame(right)
        btn_frame.pack(fill=tk.X, pady=(6, 0))

        self._run_btn = ttk.Button(
            btn_frame, text="▶  Messung starten", command=self._on_run
        )
        self._run_btn.pack(side=tk.LEFT, padx=(0, 6))
        _tip(self._run_btn, "Startet die Messung (oder Simulation)")

        self._stop_btn = ttk.Button(
            btn_frame, text="■  Stop", command=self._on_stop, state="disabled"
        )
        self._stop_btn.pack(side=tk.LEFT, padx=(0, 6))
        _tip(self._stop_btn, "Bricht die laufende Messung ab")

        self._plot_btn = ttk.Button(
            btn_frame, text="📈 Plot",
            command=self._on_show_plot, state="disabled"
        )
        self._plot_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._save_plot_btn = ttk.Button(
            btn_frame, text="💾 Plot speichern …",
            command=self._on_save_plot, state="disabled"
        )
        self._save_plot_btn.pack(side=tk.LEFT)

        ttk.Button(
            btn_frame, text="🗑 Log leeren", command=self._clear_log
        ).pack(side=tk.RIGHT)

        # ── Status bar ───────────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Bereit")
        self._status_lbl = ttk.Label(
            self,
            textvariable=self._status_var,
            relief=tk.SUNKEN,
            anchor="w",
            padding=(6, 2),
        )
        self._status_lbl.pack(fill=tk.X, side=tk.BOTTOM, padx=8, pady=(2, 4))

    # ----------------------------------------------------------------------- #
    #  Tab: Verbindung
    # ----------------------------------------------------------------------- #

    def _build_tab_connection(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Verbindung")

        ttk.Label(tab, text="Verbindungseinstellungen",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        self._simulate_var = tk.BooleanVar(value=True)
        f = ttk.Frame(tab)
        f.pack(fill=tk.X, padx=8, pady=4)
        chk = ttk.Checkbutton(
            f,
            text="Simulationsmodus  (keine Hardware erforderlich)",
            variable=self._simulate_var,
            command=self._on_simulate_toggle,
        )
        chk.pack(side=tk.LEFT)
        _tip(chk, "Aktiviert den Software-Simulator (MockInstrument) — keine echte Hardware nötig")

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        # VISA address row with scan button
        addr_frame = ttk.Frame(tab)
        addr_frame.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(addr_frame, text="VISA-Adresse:", width=16, anchor="w").pack(side=tk.LEFT)

        self._address_var = tk.StringVar()
        self._address_combo = ttk.Combobox(
            addr_frame, textvariable=self._address_var, state="disabled", width=34
        )
        self._address_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _tip(self._address_combo,
             "VISA-Ressourcen-String, z. B.:\n"
             "USB0::0x0957::0x8B18::MY12345::INSTR\n"
             "GPIB0::23::INSTR\nTCPIP0::192.168.1.10::inst0::INSTR")

        self._scan_btn = ttk.Button(
            addr_frame, text="🔍", width=3,
            command=self._on_scan_visa, state="disabled"
        )
        self._scan_btn.pack(side=tk.LEFT, padx=(4, 0))
        _tip(self._scan_btn, "Sucht alle verfügbaren VISA-Ressourcen")

        ttk.Label(
            tab,
            text="Beispiel:  USB0::0x0957::0x8B18::MY12345::INSTR",
            foreground="gray", font=("", 8),
        ).pack(anchor="w", padx=30, pady=(0, 4))

        # VISA backend info
        if not _PYVISA_OK:
            ttk.Label(
                tab,
                text="⚠ pyvisa nicht installiert — Scan nicht verfügbar.",
                foreground="#e0a000", font=("", 8),
            ).pack(anchor="w", padx=8, pady=(4, 0))

    # ----------------------------------------------------------------------- #
    #  Tab: Sweep
    # ----------------------------------------------------------------------- #

    def _build_tab_sweep(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Sweep")

        ttk.Label(tab, text="Sweep-Parameter",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        self._v_start  = self._labeled_entry(tab, "V Start (V):",            default="-50",
                                              tooltip="Startwert der Spannung in Volt")
        self._v_stop   = self._labeled_entry(tab, "V Stop (V):",             default="50",
                                              tooltip="Endwert der Spannung in Volt")
        self._points   = self._labeled_entry(tab, "Messpunkte:",             default="1000",
                                              tooltip="Anzahl der Messpunkte (2 … 100 000)")
        self._trig_int = self._labeled_entry(tab, "Trigger-Intervall (µs):", default="200",
                                              tooltip="Zeit zwischen aufeinanderfolgenden Triggern (min. 50 µs)")

    # ----------------------------------------------------------------------- #
    #  Tab: Instrument
    # ----------------------------------------------------------------------- #

    def _build_tab_instrument(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Instrument")

        ttk.Label(tab, text="Instrument-Einstellungen",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        self._compliance = self._labeled_entry(tab, "Compliance (mA):", default="100",
                                               tooltip="Maximaler Strom (Schutzgrenze) in Milliampere")
        self._nplc       = self._labeled_entry(tab, "NPLC:",             default="1.0",
                                               tooltip="Integrationszeit in Netzperioden (0.001 … 10). "
                                                       "Größer = rauschärmer, langsamer.")

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        self._high_cap_var     = tk.BooleanVar(value=False)
        self._remote_sense_var = tk.BooleanVar(value=False)

        f1 = ttk.Frame(tab); f1.pack(fill=tk.X, padx=8, pady=3)
        chk1 = ttk.Checkbutton(f1, text="High Capacitance Mode  (:SENS:CURR:HCAP ON)",
                        variable=self._high_cap_var)
        chk1.pack(side=tk.LEFT)
        _tip(chk1, "Aktivieren wenn kapazitive Last (lange Koaxkabel, große Sondenfläche)")

        f2 = ttk.Frame(tab); f2.pack(fill=tk.X, padx=8, pady=3)
        chk2 = ttk.Checkbutton(f2, text="4-Draht Kelvin-Messung  (:SYST:RSEN ON)",
                        variable=self._remote_sense_var)
        chk2.pack(side=tk.LEFT)
        _tip(chk2, "Kompensiert Leitungswiderstand — empfohlen für genaue I(V)-Messung")

    # ----------------------------------------------------------------------- #
    #  Tab: Analyse
    # ----------------------------------------------------------------------- #

    def _build_tab_analysis(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Analyse")

        ttk.Label(tab, text="Physik-Analyse",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        self._ion_start  = self._labeled_entry(tab, "Ion-Fit Start (V):", default="-50",
                                               tooltip="Linke Grenze des Ion-Sättigungsbereichs (< V_fl)")
        self._ion_stop   = self._labeled_entry(tab, "Ion-Fit Stop (V):",  default="-30",
                                               tooltip="Rechte Grenze des Ion-Sättigungsbereichs (< V_fl)")
        self._savgol_win = self._labeled_entry(tab, "Filter-Fenster:",    default="51",
                                               tooltip="Fensterlänge für den gewählten Filter (ungerade Zahl)")

        # Filter method selector
        fm_frame = ttk.Frame(tab)
        fm_frame.pack(fill=tk.X, padx=8, pady=(6, 2))
        ttk.Label(fm_frame, text="Filter-Methode:", width=22, anchor="w").pack(side=tk.LEFT)
        self._filter_var = tk.StringVar(value="savgol")
        fm_combo = ttk.Combobox(
            fm_frame, textvariable=self._filter_var,
            values=list(FILTER_METHODS), state="readonly", width=14,
        )
        fm_combo.pack(side=tk.LEFT)
        _tip(fm_combo,
             "savgol   – Savitzky-Golay (Standard, bewahrt Kurvenform)\n"
             "gaussian – Gaußsche Glättung (sanft)\n"
             "moving   – Gleitender Mittelwert (einfach)\n"
             "median   – Medianfilter (robust bei Ausreißern)\n"
             "none     – Keine Filterung (Rohdaten)")

        ttk.Label(
            tab,
            text=(
                "Ion-Fit-Bereich sollte tief im Ion-\n"
                "Sättigungsplateau liegen (< V_fl)."
            ),
            foreground="gray",
            font=("", 8),
            justify=tk.LEFT,
        ).pack(anchor="w", padx=8, pady=(8, 0))

    # ----------------------------------------------------------------------- #
    #  Tab: Ausgabe
    # ----------------------------------------------------------------------- #

    def _build_tab_output(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Ausgabe")

        ttk.Label(tab, text="Ausgabe-Einstellungen",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        f = ttk.Frame(tab)
        f.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(f, text="Ausgabe-Ordner:", width=20, anchor="w").pack(side=tk.LEFT)
        self._output_dir = ttk.Entry(f)
        self._output_dir.insert(0, "measurements")
        self._output_dir.pack(side=tk.LEFT, fill=tk.X, expand=True)
        browse_btn = ttk.Button(f, text="…", width=3, command=self._browse_output)
        browse_btn.pack(side=tk.LEFT, padx=(4, 0))
        _tip(browse_btn, "Zielordner für CSV-Dateien auswählen")

        ttk.Label(
            tab,
            text="CSV-Dateien werden mit Zeitstempel im\ngewählten Ordner gespeichert.",
            foreground="gray",
            font=("", 8),
            justify=tk.LEFT,
        ).pack(anchor="w", padx=8, pady=(8, 0))

    # ======================================================================= #
    #  Helper — labeled entry row
    # ======================================================================= #

    def _labeled_entry(
        self,
        parent: tk.Widget,
        label: str,
        default: str = "",
        state: str = "normal",
        tooltip: str = "",
    ) -> ttk.Entry:
        f = ttk.Frame(parent)
        f.pack(fill=tk.X, padx=8, pady=3)
        lbl = ttk.Label(f, text=label, width=22, anchor="w")
        lbl.pack(side=tk.LEFT)
        e = ttk.Entry(f, state=state)
        e.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if default:
            e.insert(0, default)
        if tooltip:
            _tip(e, tooltip)
            _tip(lbl, tooltip)
        return e

    # ======================================================================= #
    #  Event handlers
    # ======================================================================= #

    def _on_simulate_toggle(self) -> None:
        sim = self._simulate_var.get()
        addr_state = "disabled" if sim else "readonly"
        btn_state  = "disabled" if sim else "normal"
        self._address_combo.configure(state=addr_state)
        self._scan_btn.configure(state=btn_state)

    def _browse_output(self) -> None:
        d = filedialog.askdirectory(title="Ausgabe-Ordner wählen")
        if d:
            self._output_dir.delete(0, tk.END)
            self._output_dir.insert(0, d)

    def _on_scan_visa(self) -> None:
        """Scan for VISA resources in a background thread."""
        self._scan_btn.configure(state="disabled")
        self._set_status("Suche VISA-Ressourcen …")
        self._log("Suche VISA-Ressourcen …\n", _TAG_INFO)
        threading.Thread(target=self._do_visa_scan, daemon=True).start()

    def _do_visa_scan(self) -> None:
        resources: list[str] = []
        if _PYVISA_OK:
            try:
                rm = _pyvisa.ResourceManager()
                resources = list(rm.list_resources())
                rm.close()
            except Exception as exc:
                self.after(0, lambda: self._log(f"[VISA-Scan] Fehler: {exc}\n", _TAG_WARN))
        else:
            self.after(0, lambda: self._log(
                "[VISA-Scan] pyvisa nicht installiert.\n", _TAG_WARN))

        def _update() -> None:
            if resources:
                self._address_combo.configure(values=resources)
                self._address_combo.set(resources[0])
                self._log(f"[VISA-Scan] {len(resources)} Gerät(e) gefunden:\n", _TAG_OK)
                for r in resources:
                    self._log(f"   {r}\n", _TAG_OK)
            else:
                self._address_combo.configure(values=[])
                self._log("[VISA-Scan] Keine VISA-Ressourcen gefunden.\n", _TAG_WARN)
            self._scan_btn.configure(state="normal")
            self._set_status("Bereit")

        self.after(0, _update)

    def _on_run(self) -> None:
        if self._running:
            return
        self._running = True
        self._run_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._plot_btn.configure(state="disabled")
        self._save_plot_btn.configure(state="disabled")
        self._plotter = None
        self._clear_log()
        self._progress.start(12)
        threading.Thread(target=self._run_measurement, daemon=True).start()

    def _on_stop(self) -> None:
        """Signal that the user wants to abort (best-effort — sweep may already be running)."""
        self._log("[Stop] Abbruch angefordert (Sweep kann nicht unterbrochen werden).\n",
                  _TAG_WARN)
        self._running = False

    def _on_show_plot(self) -> None:
        if self._plotter is None:
            return
        if self._plot_win is not None and self._plot_win.winfo_exists():
            self._plot_win.lift()
            return
        self._open_plot_window()

    def _on_save_plot(self) -> None:
        if self._plotter is None:
            return
        path = filedialog.asksaveasfilename(
            title="Plot speichern",
            defaultextension=".png",
            filetypes=[
                ("PNG-Bild", "*.png"),
                ("PDF-Dokument", "*.pdf"),
                ("SVG-Vektorgrafik", "*.svg"),
            ],
        )
        if path:
            self._plotter.save(path)
            self._log(f"[Plot] Gespeichert: {path}\n", _TAG_OK)

    def _clear_log(self) -> None:
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", tk.END)
        self._log_box.configure(state="disabled")

    # ======================================================================= #
    #  Plot window
    # ======================================================================= #

    def _open_plot_window(self) -> None:
        win = tk.Toplevel(self)
        win.title("Langmuir I-V  —  Analyse-Ergebnis")
        win.geometry("1100x780")
        self._plot_win = win

        fig = self._plotter.build()

        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.draw()

        toolbar_frame = ttk.Frame(win)
        toolbar_frame.pack(side=tk.TOP, fill=tk.X)
        NavigationToolbar2Tk(canvas, toolbar_frame).update()

        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # ======================================================================= #
    #  Thread-safe helpers
    # ======================================================================= #

    def _log(self, text: str, tag: str = _TAG_INFO) -> None:
        def _append() -> None:
            self._log_box.configure(state="normal")
            self._log_box.insert(tk.END, text, tag)
            self._log_box.see(tk.END)
            self._log_box.configure(state="disabled")
        self.after(0, _append)

    def _set_status(self, text: str, colour: str = "") -> None:
        def _upd():
            self._status_var.set(text)
            if colour:
                self._status_lbl.configure(foreground=colour)
            else:
                self._status_lbl.configure(foreground="")
        self.after(0, _upd)

    # ======================================================================= #
    #  Measurement thread
    # ======================================================================= #

    def _run_measurement(self) -> None:
        try:
            self._do_run()
        except Exception:
            tb = traceback.format_exc()
            self._log(f"\n[FEHLER] Unerwarteter Fehler:\n{tb}\n", _TAG_ERR)
            self._set_status("Fehler — Details im Log", "red")
        finally:
            self._running = False
            self.after(0, self._progress.stop)
            self.after(0, lambda: self._run_btn.configure(state="normal"))
            self.after(0, lambda: self._stop_btn.configure(state="disabled"))

    def _do_run(self) -> None:
        # ── Parse & validate parameters ──────────────────────────────────
        try:
            v_start    = float(self._v_start.get())
            v_stop     = float(self._v_stop.get())
            n_points   = int(self._points.get())
            trig_int   = float(self._trig_int.get()) * 1e-6   # µs → s
            compliance = float(self._compliance.get()) / 1000.0  # mA → A
            nplc       = float(self._nplc.get())
            ion_start  = float(self._ion_start.get())
            ion_stop   = float(self._ion_stop.get())
            savgol_win = int(self._savgol_win.get())
            output_dir = self._output_dir.get().strip() or "measurements"
        except ValueError as exc:
            self._log(f"[FEHLER] Ungültiger Parameter-Wert: {exc}\n", _TAG_ERR)
            self._set_status("Fehler — ungültige Eingabe", "red")
            return

        filter_method = self._filter_var.get()
        simulate      = self._simulate_var.get()
        address       = self._address_var.get().strip() or None

        self._log("=" * 55 + "\n", _TAG_HEAD)
        self._log("  Langmuir Probe Framework  |  Keysight B2910BL  v2\n", _TAG_HEAD)
        self._log("=" * 55 + "\n", _TAG_HEAD)
        self._log(f"  Filter: {filter_method}   Fenster: {savgol_win}\n", _TAG_INFO)
        self._log("=" * 55 + "\n", _TAG_HEAD)

        # ── Step 1 — Verbinden ───────────────────────────────────────────
        self._set_status("Verbinde …")
        manager = ConnectionManager(simulate=simulate)
        try:
            instrument = manager.connect(address=address)
        except ConnectionError as exc:
            self._log(f"\n[FEHLER] Verbindung fehlgeschlagen: {exc}\n", _TAG_ERR)
            self._set_status("Verbindungsfehler", "red")
            return

        # ── Step 2 — Sweep ───────────────────────────────────────────────
        driver = B2910BLDriver(
            instrument=instrument,
            compliance_current=compliance,
            nplc=nplc,
            high_cap_mode=self._high_cap_var.get(),
            remote_sensing=self._remote_sense_var.get(),
        )

        try:
            idn = driver.identify()
            self._log(f"\n[Instrument] {idn}\n", _TAG_INFO)
            driver.reset()
            driver.configure()

            self._log(
                f"\n[Sweep]  {v_start:+.1f} V → {v_stop:+.1f} V  |  "
                f"{n_points} Punkte  |  NPLC={nplc}  |  "
                f"Compliance={compliance * 1e3:.0f} mA\n",
                _TAG_INFO,
            )
            if self._high_cap_var.get():
                self._log("[Sweep]  High Capacitance Mode: EIN\n", _TAG_WARN)
            if self._remote_sense_var.get():
                self._log("[Sweep]  4-Draht Remote Sensing: EIN\n", _TAG_INFO)

            self._set_status("Sweep läuft …")

            V, I = driver.run_sweep(
                v_start=v_start,
                v_stop=v_stop,
                n_points=n_points,
                trigger_interval=trig_int,
            )
            self._log(f"[Sweep]  Abgeschlossen — {len(V)} Punkte erfasst.\n", _TAG_OK)

        except (ValueError, RuntimeError) as exc:
            self._log(f"\n[FEHLER] Sweep fehlgeschlagen: {exc}\n", _TAG_ERR)
            self._set_status("Sweep-Fehler", "red")
            manager.disconnect()
            return
        finally:
            manager.disconnect()

        # ── Step 3 — Rohdaten speichern ──────────────────────────────────
        self._set_status("Speichere Rohdaten …")
        raw_path = save_raw_data(V, I, directory=output_dir)
        self._log(f"[Export]  Rohdaten:   {raw_path}\n", _TAG_INFO)

        # ── Step 4 — Physik-Analyse ──────────────────────────────────────
        self._set_status("Physik-Analyse …")
        self._log("\n[Analyse]  Starte Physik-Pipeline …\n", _TAG_INFO)
        self._log(f"[Analyse]  Filter-Methode: {filter_method}  "
                  f"Fenster: {savgol_win}\n", _TAG_INFO)

        analyzer = LangmuirAnalyzer(
            V=V,
            I=I,
            savgol_window=savgol_win,
            ion_fit_range=(ion_start, ion_stop),
            filter_method=filter_method,
        )
        try:
            results = analyzer.analyze()
        except AnalysisError as exc:
            self._log(f"\n[FEHLER] Analyse fehlgeschlagen: {exc}\n", _TAG_ERR)
            self._log(
                "Tipp: Ion-Fit-Bereich (Tab 'Analyse') oder "
                "Sweep-Bereich anpassen.\n",
                _TAG_WARN,
            )
            self._set_status("Analyse-Fehler", "red")
            return

        self._log(results.summary() + "\n", _TAG_OK)

        # ── Step 5 — Ergebnisse speichern ────────────────────────────────
        sweep_params = {
            "v_start": v_start,
            "v_stop": v_stop,
            "n_points": n_points,
            "compliance_A": compliance,
            "nplc": nplc,
            "filter_method": filter_method,
            "filter_window": savgol_win,
            "high_cap": self._high_cap_var.get(),
            "remote_sense": self._remote_sense_var.get(),
        }
        res_path = save_results(results, directory=output_dir, sweep_params=sweep_params)
        self._log(f"[Export]  Ergebnisse: {res_path}\n", _TAG_INFO)

        # ── Step 6 — Plotter vorbereiten ─────────────────────────────────
        self._plotter = LangmuirPlotter(V, I, analyzer, results)

        self._log("\n[Fertig]  Alle Schritte erfolgreich abgeschlossen.\n", _TAG_OK)
        self._set_status("Fertig  —  Plot bereit", "green")

        self.after(0, lambda: self._plot_btn.configure(state="normal"))
        self.after(0, lambda: self._save_plot_btn.configure(state="normal"))
        self.after(0, self._open_plot_window)   # auto-open plot on success


# ============================================================================ #
#  Entry point
# ============================================================================ #

if __name__ == "__main__":
    app = LangmuirGUI()
    app.mainloop()
