"""
Langmuir Probe Framework  v2  —  GUI
======================================
Start via double-click on  'Start Langmuir GUI.bat'
or directly:  python main_v2_gui.py
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
from tkinter import ttk, filedialog, scrolledtext, messagebox

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

# ── Make shared framework modules importable from v2 as well ───────────────
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from hardware.connection_manager import ConnectionManager
from hardware.b2910bl_driver import B2910BLDriver
from physics.langmuir_analysis import LangmuirAnalyzer, AnalysisError
from visualization.plotter import LangmuirPlotter
from utils.data_export import save_raw_data, save_results


# ============================================================================ #
#  Main application window
# ============================================================================ #

class LangmuirGUI(tk.Tk):

    def __init__(self) -> None:
        super().__init__()
        self.title("Langmuir Probe Framework  v2")
        self.geometry("1000x680")
        self.minsize(800, 560)
        self.resizable(True, True)

        # ── Runtime state ────────────────────────────────────────────────
        self._running    = False
        self._plotter: LangmuirPlotter | None = None
        self._plot_win: tk.Toplevel | None    = None

        self._build_ui()
        self._log("Bereit.  Einstellungen anpassen und '▶  Messung starten' klicken.\n")

    # ======================================================================= #
    #  UI construction
    # ======================================================================= #

    def _build_ui(self) -> None:
        # ── Top-level layout: settings left | log right ──────────────────
        pw = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))

        # Left column — settings notebook
        left = ttk.Frame(pw, width=320)
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
            font=("Courier New", 9),
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="white",
            selectbackground="#264f78",
        )
        self._log_box.pack(fill=tk.BOTH, expand=True)

        # ── Button row ───────────────────────────────────────────────────
        btn_frame = ttk.Frame(right)
        btn_frame.pack(fill=tk.X, pady=(6, 0))

        self._run_btn = ttk.Button(
            btn_frame, text="▶  Messung starten", command=self._on_run
        )
        self._run_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._plot_btn = ttk.Button(
            btn_frame, text="Plot anzeigen",
            command=self._on_show_plot, state="disabled"
        )
        self._plot_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._save_plot_btn = ttk.Button(
            btn_frame, text="Plot speichern …",
            command=self._on_save_plot, state="disabled"
        )
        self._save_plot_btn.pack(side=tk.LEFT)

        ttk.Button(
            btn_frame, text="Log leeren", command=self._clear_log
        ).pack(side=tk.RIGHT)

        # ── Status bar ───────────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Bereit")
        ttk.Label(
            self,
            textvariable=self._status_var,
            relief=tk.SUNKEN,
            anchor="w",
            padding=(6, 2),
        ).pack(fill=tk.X, side=tk.BOTTOM, padx=8, pady=(2, 4))

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
        ttk.Checkbutton(
            f,
            text="Simulationsmodus  (keine Hardware erforderlich)",
            variable=self._simulate_var,
            command=self._on_simulate_toggle,
        ).pack(side=tk.LEFT)

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        self._address_entry = self._labeled_entry(
            tab, "VISA-Adresse:", state="disabled"
        )
        ttk.Label(
            tab,
            text="Beispiel:  USB0::0x0957::0x8B18::MY12345::INSTR",
            foreground="gray",
            font=("", 8),
        ).pack(anchor="w", padx=30, pady=(0, 4))

    # ----------------------------------------------------------------------- #
    #  Tab: Sweep
    # ----------------------------------------------------------------------- #

    def _build_tab_sweep(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Sweep")

        ttk.Label(tab, text="Sweep-Parameter",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        self._v_start  = self._labeled_entry(tab, "V Start (V):",          default="-50")
        self._v_stop   = self._labeled_entry(tab, "V Stop (V):",           default="50")
        self._points   = self._labeled_entry(tab, "Messpunkte:",           default="1000")
        self._trig_int = self._labeled_entry(tab, "Trigger-Intervall (µs):", default="200")

    # ----------------------------------------------------------------------- #
    #  Tab: Instrument
    # ----------------------------------------------------------------------- #

    def _build_tab_instrument(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Instrument")

        ttk.Label(tab, text="Instrument-Einstellungen",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        self._compliance = self._labeled_entry(tab, "Compliance (mA):", default="100")
        self._nplc       = self._labeled_entry(tab, "NPLC:",             default="1.0")

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        self._high_cap_var     = tk.BooleanVar(value=False)
        self._remote_sense_var = tk.BooleanVar(value=False)

        f1 = ttk.Frame(tab); f1.pack(fill=tk.X, padx=8, pady=3)
        ttk.Checkbutton(f1, text="High Capacitance Mode  (:SENS:CURR:HCAP ON)",
                        variable=self._high_cap_var).pack(side=tk.LEFT)

        f2 = ttk.Frame(tab); f2.pack(fill=tk.X, padx=8, pady=3)
        ttk.Checkbutton(f2, text="4-Draht Kelvin-Messung  (:SYST:RSEN ON)",
                        variable=self._remote_sense_var).pack(side=tk.LEFT)

    # ----------------------------------------------------------------------- #
    #  Tab: Analyse
    # ----------------------------------------------------------------------- #

    def _build_tab_analysis(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Analyse")

        ttk.Label(tab, text="Physik-Analyse",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        self._ion_start  = self._labeled_entry(tab, "Ion-Fit Start (V):", default="-50")
        self._ion_stop   = self._labeled_entry(tab, "Ion-Fit Stop (V):",  default="-30")
        self._savgol_win = self._labeled_entry(tab, "Savgol-Fenster:",     default="51")

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

        # Output directory row with browse button
        f = ttk.Frame(tab)
        f.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(f, text="Ausgabe-Ordner:", width=20, anchor="w").pack(side=tk.LEFT)
        self._output_dir = ttk.Entry(f)
        self._output_dir.insert(0, "measurements")
        self._output_dir.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(f, text="…", width=3,
                   command=self._browse_output).pack(side=tk.LEFT, padx=(4, 0))

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
    ) -> ttk.Entry:
        f = ttk.Frame(parent)
        f.pack(fill=tk.X, padx=8, pady=3)
        ttk.Label(f, text=label, width=22, anchor="w").pack(side=tk.LEFT)
        e = ttk.Entry(f, state=state)
        e.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if default:
            e.insert(0, default)
        return e

    # ======================================================================= #
    #  Event handlers
    # ======================================================================= #

    def _on_simulate_toggle(self) -> None:
        state = "disabled" if self._simulate_var.get() else "normal"
        self._address_entry.configure(state=state)

    def _browse_output(self) -> None:
        d = filedialog.askdirectory(title="Ausgabe-Ordner wählen")
        if d:
            self._output_dir.delete(0, tk.END)
            self._output_dir.insert(0, d)

    def _on_run(self) -> None:
        if self._running:
            return
        self._running = True
        self._run_btn.configure(state="disabled")
        self._plot_btn.configure(state="disabled")
        self._save_plot_btn.configure(state="disabled")
        self._plotter = None
        self._clear_log()
        threading.Thread(target=self._run_measurement, daemon=True).start()

    def _on_show_plot(self) -> None:
        if self._plotter is None:
            return
        # If already open, bring to front
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
            self._log(f"[Plot] Gespeichert: {path}\n")

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

    def _log(self, text: str) -> None:
        def _append() -> None:
            self._log_box.configure(state="normal")
            self._log_box.insert(tk.END, text)
            self._log_box.see(tk.END)
            self._log_box.configure(state="disabled")
        self.after(0, _append)

    def _set_status(self, text: str) -> None:
        self.after(0, lambda: self._status_var.set(text))

    # ======================================================================= #
    #  Measurement thread
    # ======================================================================= #

    def _run_measurement(self) -> None:
        try:
            self._do_run()
        except Exception:
            self._log(f"\n[FEHLER] Unerwarteter Fehler:\n{traceback.format_exc()}\n")
            self._set_status("Fehler — Details im Log")
        finally:
            self._running = False
            self.after(0, lambda: self._run_btn.configure(state="normal"))

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
            self._log(f"[FEHLER] Ungültiger Parameter-Wert: {exc}\n")
            self._set_status("Fehler — ungültige Eingabe")
            return

        simulate = self._simulate_var.get()
        address  = self._address_entry.get().strip() or None

        self._log("=" * 55 + "\n")
        self._log("  Langmuir Probe Framework  |  Keysight B2910BL  v2\n")
        self._log("=" * 55 + "\n")

        # ── Step 1 — Verbinden ───────────────────────────────────────────
        self._set_status("Verbinde …")
        manager = ConnectionManager(simulate=simulate)
        try:
            instrument = manager.connect(address=address)
        except ConnectionError as exc:
            self._log(f"\n[FEHLER] Verbindung fehlgeschlagen: {exc}\n")
            self._set_status("Verbindungsfehler")
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
            self._log(f"\n[Instrument] {driver.identify()}\n")
            driver.reset()
            driver.configure()

            self._log(
                f"\n[Sweep]  {v_start:+.1f} V → {v_stop:+.1f} V  |  "
                f"{n_points} Punkte  |  NPLC={nplc}  |  "
                f"Compliance={compliance * 1e3:.0f} mA\n"
            )
            if self._high_cap_var.get():
                self._log("[Sweep]  High Capacitance Mode: EIN\n")
            if self._remote_sense_var.get():
                self._log("[Sweep]  4-Draht Remote Sensing: EIN\n")

            self._set_status("Sweep läuft …")

            V, I = driver.run_sweep(
                v_start=v_start,
                v_stop=v_stop,
                n_points=n_points,
                trigger_interval=trig_int,
            )
            self._log(f"[Sweep]  Abgeschlossen — {len(V)} Punkte erfasst.\n")

        except (ValueError, RuntimeError) as exc:
            self._log(f"\n[FEHLER] Sweep fehlgeschlagen: {exc}\n")
            self._set_status("Sweep-Fehler")
            manager.disconnect()
            return
        finally:
            manager.disconnect()

        # ── Step 3 — Rohdaten speichern ──────────────────────────────────
        self._set_status("Speichere Rohdaten …")
        raw_path = save_raw_data(V, I, directory=output_dir)
        self._log(f"[Export]  Rohdaten:   {raw_path}\n")

        # ── Step 4 — Physik-Analyse ──────────────────────────────────────
        self._set_status("Physik-Analyse …")
        self._log("\n[Analyse]  Starte Physik-Pipeline …\n")

        analyzer = LangmuirAnalyzer(
            V=V,
            I=I,
            savgol_window=savgol_win,
            ion_fit_range=(ion_start, ion_stop),
        )
        try:
            results = analyzer.analyze()
        except AnalysisError as exc:
            self._log(f"\n[FEHLER] Analyse fehlgeschlagen: {exc}\n")
            self._log(
                "Tipp: Ion-Fit-Bereich (Tab 'Analyse') oder "
                "Sweep-Bereich anpassen.\n"
            )
            self._set_status("Analyse-Fehler")
            return

        self._log(results.summary() + "\n")

        # ── Step 5 — Ergebnisse speichern ────────────────────────────────
        sweep_params = {
            "v_start": v_start,
            "v_stop": v_stop,
            "n_points": n_points,
            "compliance_A": compliance,
            "nplc": nplc,
            "high_cap": self._high_cap_var.get(),
            "remote_sense": self._remote_sense_var.get(),
        }
        res_path = save_results(results, directory=output_dir, sweep_params=sweep_params)
        self._log(f"[Export]  Ergebnisse: {res_path}\n")

        # ── Step 6 — Plotter vorbereiten ─────────────────────────────────
        self._plotter = LangmuirPlotter(V, I, analyzer, results)

        self._log("\n[Fertig]  Alle Schritte erfolgreich abgeschlossen.\n")
        self._set_status("Fertig  —  Plot bereit")

        self.after(0, lambda: self._plot_btn.configure(state="normal"))
        self.after(0, lambda: self._save_plot_btn.configure(state="normal"))
        self.after(0, self._open_plot_window)   # auto-open plot on success


# ============================================================================ #
#  Entry point
# ============================================================================ #

if __name__ == "__main__":
    app = LangmuirGUI()
    app.mainloop()
