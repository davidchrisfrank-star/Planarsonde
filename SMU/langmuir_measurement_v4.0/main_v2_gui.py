"""
Langmuir-Sonden Messsystem – grafische Benutzeroberfläche
=========================================================
Starten per Doppelklick auf 'Start Langmuir GUI.bat'
oder direkt:  python main_v2_gui.py

Funktionen:
  • Keysight B2910BL SMU – vollständiger Sweep + Physikanalyse
  • Keysight 34923A MUX – Sondenkanäle über die GUI schalten
  • VISA-Gerätescanner mit Aktualisierungsschaltfläche (SMU und MUX)
  • Filtermethoden-Auswahl (Savitzky-Golay / Gauß / Gleitender Mittel. / Median / Keine)
  • Fortschrittsbalken während des Sweeps
  • Farbcodierte Statusleiste
  • Dunkler Log-Bereich mit farbig markierten Meldungen
  • Tooltips an allen wichtigen Steuerelementen

Automatischer Messablauf:
  1. MUX ABus-Kanal schließen (Bankbrücke)
  2. MUX Sondenkanal schließen
  3. SMU Spannungs-Sweep durchführen
  4. MUX Sondenkanal öffnen (optional)
  5. Physikalische Auswertung
  6. CSV-Export
  7. Plot anzeigen
"""

from __future__ import annotations

# Matplotlib-Backend muss vor jedem pyplot-Import gesetzt werden
import matplotlib
matplotlib.use("TkAgg")

import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

# Eigene Module aus dem gleichen Verzeichnis importierbar machen
_HIER = Path(__file__).resolve().parent
if str(_HIER) not in sys.path:
    sys.path.insert(0, str(_HIER))

from hardware.connection_manager import ConnectionManager
from hardware.b2910bl_driver import B2910BLDriver
from hardware.mux_34923a_driver import Mux34923ADriver
from physics.langmuir_analysis import LangmuirAnalyzer, AnalysisError, FILTER_METHODS
from visualization.plotter import LangmuirPlotter
from utils.data_export import save_raw_data, save_results

# pyvisa für den VISA-Scanner (optional – falls nicht installiert wird der Scan deaktiviert)
try:
    import pyvisa as _pyvisa        # type: ignore
    _PYVISA_VORHANDEN = True
except ImportError:
    _PYVISA_VORHANDEN = False

# ── Erscheinungsbild ────────────────────────────────────────────────────────
_HINTERGRUND  = "#1e1e1e"   # Dunkler Hintergrund für den Log-Bereich
_VORDERGRUND  = "#d4d4d4"   # Helle Schrift
_AUSWAHL      = "#264f78"   # Blau für Textauswahl
_SCHRIFT      = ("Courier New", 9)

# Log-Farbtags (werden im ScrolledText registriert)
_TAG_INFO   = "info"    # Grau  – normale Informationen
_TAG_OK     = "ok"      # Grün  – Erfolg
_TAG_WARN   = "warn"    # Gelb  – Warnung
_TAG_ERR    = "err"     # Rot   – Fehler
_TAG_KOPF   = "kopf"   # Cyan  – Überschriften
_TAG_MUX    = "mux"    # Orange – MUX-Meldungen


# ============================================================================ #
#  Tooltip-Hilfsklasse
# ============================================================================ #

class _Tooltip:
    """Zeigt einen schwebenden Hilfstext wenn der Mauszeiger über einem Widget verweilt."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text   = text
        self._fenster: tk.Toplevel | None = None
        widget.bind("<Enter>", self._anzeigen)
        widget.bind("<Leave>", self._verbergen)

    def _anzeigen(self, _=None) -> None:
        """Tooltip-Fenster direkt unterhalb des Widgets anzeigen."""
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._fenster = tk.Toplevel(self._widget)
        self._fenster.wm_overrideredirect(True)   # Kein Fensterrahmen
        self._fenster.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._fenster, text=self._text,
            background="#ffffe0", relief="solid", borderwidth=1,
            font=("TkDefaultFont", 8), wraplength=320, justify="left",
        ).pack(ipadx=4, ipady=2)

    def _verbergen(self, _=None) -> None:
        """Tooltip-Fenster schließen."""
        if self._fenster:
            self._fenster.destroy()
            self._fenster = None


def _tooltip(widget: tk.Widget, text: str) -> None:
    """Bequeme Hilfsfunktion zum Anlegen eines Tooltips."""
    _Tooltip(widget, text)


# ============================================================================ #
#  VISA-Scanner (gemeinsam für SMU und MUX)
# ============================================================================ #

def _visa_ressourcen_suchen() -> list[str]:
    """
    Alle verfügbaren VISA-Ressourcen zurückgeben.
    Bei fehlender pyvisa-Installation oder Fehler: leere Liste.
    """
    if not _PYVISA_VORHANDEN:
        return []
    try:
        rm        = _pyvisa.ResourceManager()
        ressourcen = list(rm.list_resources())
        rm.close()
        return ressourcen
    except Exception:
        return []


# ============================================================================ #
#  Hauptfenster
# ============================================================================ #

class LangmuirGUI(tk.Tk):
    """Hauptfenster der Langmuirsonden-Messanwendung."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Langmuirsonden-Messsystem  |  SMU + MUX")
        self.geometry("1180x740")
        self.minsize(920, 600)
        self.resizable(True, True)

        # ── Laufzeitzustand ───────────────────────────────────────────────
        self._laeuft: bool                           = False   # Messung läuft gerade
        self._plotter: LangmuirPlotter | None        = None    # Letzter Plotter
        self._plot_fenster: tk.Toplevel | None       = None    # Letztes Plot-Fenster
        self._mux_treiber: Mux34923ADriver | None    = None    # Aktive MUX-Verbindung

        self._oberflaeche_aufbauen()
        self._log("Bereit. Einstellungen anpassen und '▶ Messung starten' klicken.\n",
                  _TAG_INFO)

    # ======================================================================= #
    #  Oberfläche aufbauen
    # ======================================================================= #

    def _oberflaeche_aufbauen(self) -> None:
        """Alle Widgets erzeugen und anordnen."""
        # Geteiltes Layout: Einstellungen links | Log rechts
        pw = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))

        # Linke Spalte – Einstellungen (feste Breite)
        links = ttk.Frame(pw, width=360)
        links.pack_propagate(False)
        pw.add(links, weight=0)

        nb = ttk.Notebook(links)
        nb.pack(fill=tk.BOTH, expand=True)
        self._tab_smu_verbindung(nb)    # Tab 1: SMU-Verbindung
        self._tab_mux(nb)               # Tab 2: MUX-Steuerung
        self._tab_sweep(nb)             # Tab 3: Sweep-Parameter
        self._tab_instrument(nb)        # Tab 4: Instrument-Einstellungen
        self._tab_analyse(nb)           # Tab 5: Physikalische Analyse
        self._tab_ausgabe(nb)           # Tab 6: Ausgabepfad

        # Rechte Spalte – Log + Schaltflächen
        rechts = ttk.Frame(pw)
        pw.add(rechts, weight=1)

        ttk.Label(rechts, text="Protokoll", font=("", 10, "bold")).pack(
            anchor="w", pady=(0, 2))

        # Dunkler Log-Bereich mit farbigen Meldungs-Tags
        self._log_feld = scrolledtext.ScrolledText(
            rechts, state="disabled", wrap=tk.WORD, font=_SCHRIFT,
            background=_HINTERGRUND, foreground=_VORDERGRUND,
            insertbackground="white", selectbackground=_AUSWAHL,
        )
        self._log_feld.tag_config(_TAG_INFO,  foreground="#d4d4d4")
        self._log_feld.tag_config(_TAG_OK,    foreground="#4ec9b0")
        self._log_feld.tag_config(_TAG_WARN,  foreground="#dcdcaa")
        self._log_feld.tag_config(_TAG_ERR,   foreground="#f44747")
        self._log_feld.tag_config(_TAG_KOPF,  foreground="#9cdcfe",
                                   font=(_SCHRIFT[0], _SCHRIFT[1], "bold"))
        self._log_feld.tag_config(_TAG_MUX,   foreground="#ce9178")
        self._log_feld.pack(fill=tk.BOTH, expand=True)

        # Fortschrittsbalken (indeterminiert – zeigt nur Aktivität an)
        self._fortschritt = ttk.Progressbar(rechts, mode="indeterminate")
        self._fortschritt.pack(fill=tk.X, pady=(4, 0))

        # Schaltflächenleiste
        schaltflaechen = ttk.Frame(rechts)
        schaltflaechen.pack(fill=tk.X, pady=(6, 0))

        self._start_btn = ttk.Button(
            schaltflaechen, text="▶  Messung starten", command=self._messung_starten)
        self._start_btn.pack(side=tk.LEFT, padx=(0, 4))
        _tooltip(self._start_btn,
                 "Startet den vollständigen Ablauf:\n"
                 "MUX ABus → MUX Sonde → SMU Sweep → Analyse → MUX öffnen")

        self._stop_btn = ttk.Button(
            schaltflaechen, text="■ Stop",
            command=self._messung_stoppen, state="disabled")
        self._stop_btn.pack(side=tk.LEFT, padx=(0, 4))
        _tooltip(self._stop_btn, "Bricht den laufenden Messvorgang ab")

        self._plot_btn = ttk.Button(
            schaltflaechen, text="📈 Plot",
            command=self._plot_anzeigen, state="disabled")
        self._plot_btn.pack(side=tk.LEFT, padx=(0, 4))

        self._plot_speichern_btn = ttk.Button(
            schaltflaechen, text="💾 Plot speichern …",
            command=self._plot_speichern, state="disabled")
        self._plot_speichern_btn.pack(side=tk.LEFT)

        ttk.Button(schaltflaechen, text="🗑 Log leeren",
                   command=self._log_leeren).pack(side=tk.RIGHT)

        # Statusleiste am unteren Rand
        self._status_var = tk.StringVar(value="Bereit")
        self._status_lbl = ttk.Label(
            self, textvariable=self._status_var,
            relief=tk.SUNKEN, anchor="w", padding=(6, 2),
        )
        self._status_lbl.pack(fill=tk.X, side=tk.BOTTOM, padx=8, pady=(2, 4))

    # ----------------------------------------------------------------------- #
    #  Tab 1: SMU-Verbindung
    # ----------------------------------------------------------------------- #

    def _tab_smu_verbindung(self, nb: ttk.Notebook) -> None:
        """Tab für die Verbindungseinstellungen des B2910BL SMU."""
        tab = ttk.Frame(nb)
        nb.add(tab, text="SMU")

        ttk.Label(tab, text="B2910BL — Verbindung",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        # Simulationsmodus-Umschalter
        self._simulation_var = tk.BooleanVar(value=True)
        f = ttk.Frame(tab)
        f.pack(fill=tk.X, padx=8, pady=4)
        chk = ttk.Checkbutton(
            f, text="Simulationsmodus  (keine Hardware erforderlich)",
            variable=self._simulation_var,
            command=self._simulation_umschalten,
        )
        chk.pack(side=tk.LEFT)
        _tooltip(chk, "Aktiviert den Software-Simulator — kein echtes SMU benötigt")

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        # VISA-Adresse mit Scan-Schaltfläche
        zeile = ttk.Frame(tab)
        zeile.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(zeile, text="VISA-Adresse:", width=16, anchor="w").pack(side=tk.LEFT)

        self._smu_adresse_var = tk.StringVar()
        self._smu_adresse_combo = ttk.Combobox(
            zeile, textvariable=self._smu_adresse_var, state="disabled", width=30)
        self._smu_adresse_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _tooltip(self._smu_adresse_combo,
                 "VISA-Ressourcen-String, z. B.:\n"
                 "USB0::0x0957::0x8B18::MY12345::INSTR\n"
                 "GPIB0::23::INSTR\nTCPIP0::192.168.1.10::inst0::INSTR")

        self._smu_scan_btn = ttk.Button(
            zeile, text="🔍", width=3,
            command=self._smu_visa_scannen, state="disabled")
        self._smu_scan_btn.pack(side=tk.LEFT, padx=(4, 0))
        _tooltip(self._smu_scan_btn, "Sucht alle verfügbaren VISA-Ressourcen")

        ttk.Label(
            tab, text="Beispiel:  USB0::0x0957::0x8B18::MY12345::INSTR",
            foreground="gray", font=("", 8),
        ).pack(anchor="w", padx=30, pady=(0, 4))

        # Warnung falls pyvisa fehlt
        if not _PYVISA_VORHANDEN:
            ttk.Label(
                tab, foreground="#e0a000", font=("", 8),
                text="⚠ pyvisa nicht installiert — automatischer Scan nicht verfügbar.",
            ).pack(anchor="w", padx=8, pady=(4, 0))

    # ----------------------------------------------------------------------- #
    #  Tab 2: MUX-Steuerung
    # ----------------------------------------------------------------------- #

    def _tab_mux(self, nb: ttk.Notebook) -> None:
        """Tab für die Steuerung des Keysight 34923A Multiplexers."""
        tab = ttk.Frame(nb)
        nb.add(tab, text="MUX 34923A")

        ttk.Label(tab, text="Keysight 34923A — Multiplexer",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        # MUX aktivieren/deaktivieren
        self._mux_aktiv_var = tk.BooleanVar(value=False)
        f_aktiv = ttk.Frame(tab)
        f_aktiv.pack(fill=tk.X, padx=8, pady=4)
        chk_aktiv = ttk.Checkbutton(
            f_aktiv, text="MUX verwenden",
            variable=self._mux_aktiv_var,
            command=self._mux_aktiv_umschalten,
        )
        chk_aktiv.pack(side=tk.LEFT)
        _tooltip(chk_aktiv,
                 "Aktiviert den Multiplexer für Kanal-Routing.\n"
                 "Falls deaktiviert, wird der MUX im Messablauf komplett ignoriert.")

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        # MUX VISA-Adresse
        zeile_addr = ttk.Frame(tab)
        zeile_addr.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(zeile_addr, text="VISA-Adresse:", width=16, anchor="w").pack(side=tk.LEFT)

        self._mux_adresse_var = tk.StringVar()
        self._mux_adresse_combo = ttk.Combobox(
            zeile_addr, textvariable=self._mux_adresse_var, state="disabled", width=30)
        self._mux_adresse_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _tooltip(self._mux_adresse_combo,
                 "VISA-Adresse des 34980A/34970A Mainframes, z. B.:\n"
                 "USB0::0x0957::0x2C07::MY12345::INSTR\n"
                 "GPIB0::9::INSTR")

        self._mux_scan_btn = ttk.Button(
            zeile_addr, text="🔍", width=3,
            command=self._mux_visa_scannen, state="disabled")
        self._mux_scan_btn.pack(side=tk.LEFT, padx=(4, 0))
        _tooltip(self._mux_scan_btn, "Sucht alle verfügbaren VISA-Ressourcen für den MUX")

        # Slot-Nummer im Mainframe
        self._mux_slot = self._eingabezeile(
            tab, "Slot-Nummer:", standard="1",
            tooltip="Slot im 34980A/34970A Mainframe, in dem die 34923A sitzt (1–8)")
        self._mux_slot.configure(state="disabled")

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        # Kanal-Konfiguration für den automatischen Messablauf
        ttk.Label(tab, text="Messablauf-Kanäle",
                  font=("", 9, "bold")).pack(anchor="w", padx=8, pady=(0, 4))

        self._mux_abus = self._eingabezeile(
            tab, "ABus-Kanal:", standard="1911",
            tooltip="Bankbrücken-Kanal (z. B. 1911 = Slot 1, Kanal 11).\n"
                    "Wird als erstes vor dem Messkanal geschlossen.\n"
                    "Leer lassen wenn kein ABus-Kanal benötigt wird.")
        self._mux_abus.configure(state="disabled")

        self._mux_sonden_kanal = self._eingabezeile(
            tab, "Sondenkanal:", standard="1001",
            tooltip="Kanal der Langmuirsonde (z. B. 1001 = Slot 1, Kanal 1).\n"
                    "Wird direkt vor dem Sweep geschlossen und danach geöffnet.")
        self._mux_sonden_kanal.configure(state="disabled")

        # Kanal nach Sweep automatisch öffnen
        self._mux_auto_oeffnen_var = tk.BooleanVar(value=True)
        f_ao = ttk.Frame(tab)
        f_ao.pack(fill=tk.X, padx=8, pady=4)
        chk_ao = ttk.Checkbutton(
            f_ao, text="Kanal nach Sweep automatisch öffnen",
            variable=self._mux_auto_oeffnen_var)
        chk_ao.pack(side=tk.LEFT)
        _tooltip(chk_ao, "Öffnet den Sondenkanal automatisch nach Abschluss des Sweeps")

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        # Manuelle Steuerung
        ttk.Label(tab, text="Manuelle Steuerung",
                  font=("", 9, "bold")).pack(anchor="w", padx=8, pady=(0, 4))

        zeile_man = ttk.Frame(tab)
        zeile_man.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(zeile_man, text="Kanal(e):", width=10, anchor="w").pack(side=tk.LEFT)
        self._mux_manuell_kanal = ttk.Entry(zeile_man, width=18)
        self._mux_manuell_kanal.insert(0, "1001")
        self._mux_manuell_kanal.pack(side=tk.LEFT, padx=(0, 4))
        self._mux_manuell_kanal.configure(state="disabled")
        _tooltip(self._mux_manuell_kanal,
                 "Kommagetrennte Kanalliste für manuelle Befehle, z. B.: 1001,1002")

        # Schaltflächen für manuelle Steuerung
        btn_zeile = ttk.Frame(tab)
        btn_zeile.pack(fill=tk.X, padx=8, pady=2)

        self._mux_verbinden_btn = ttk.Button(
            btn_zeile, text="🔗 Verbinden",
            command=self._mux_verbinden, state="disabled")
        self._mux_verbinden_btn.pack(side=tk.LEFT, padx=(0, 4))
        _tooltip(self._mux_verbinden_btn, "Verbindung zum MUX herstellen / trennen")

        self._mux_schliessen_btn = ttk.Button(
            btn_zeile, text="⬤ Schließen",
            command=self._mux_kanal_schliessen_manuell, state="disabled")
        self._mux_schliessen_btn.pack(side=tk.LEFT, padx=(0, 4))
        _tooltip(self._mux_schliessen_btn, "Gewählte(n) Kanal/Kanäle schließen (verbinden)")

        self._mux_oeffnen_btn = ttk.Button(
            btn_zeile, text="○ Öffnen",
            command=self._mux_kanal_oeffnen_manuell, state="disabled")
        self._mux_oeffnen_btn.pack(side=tk.LEFT, padx=(0, 4))
        _tooltip(self._mux_oeffnen_btn, "Gewählte(n) Kanal/Kanäle öffnen (trennen)")

        self._mux_alle_oeffnen_btn = ttk.Button(
            btn_zeile, text="⊘ Alle öffnen",
            command=self._mux_alle_oeffnen, state="disabled")
        self._mux_alle_oeffnen_btn.pack(side=tk.LEFT)
        _tooltip(self._mux_alle_oeffnen_btn, "Alle Kanäle öffnen (ROUT:OPEN:ALL)")

        # MUX-Statusanzeige
        self._mux_status_var = tk.StringVar(value="MUX: nicht verbunden")
        ttk.Label(tab, textvariable=self._mux_status_var,
                  foreground="gray", font=("", 8)).pack(
            anchor="w", padx=8, pady=(6, 0))

    # ----------------------------------------------------------------------- #
    #  Tab 3: Sweep-Parameter
    # ----------------------------------------------------------------------- #

    def _tab_sweep(self, nb: ttk.Notebook) -> None:
        """Tab für die Sweep-Parameter des SMU."""
        tab = ttk.Frame(nb)
        nb.add(tab, text="Sweep")

        ttk.Label(tab, text="Sweep-Parameter",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        self._v_start  = self._eingabezeile(tab, "V Start (V):",            standard="-50",
                                             tooltip="Sweep-Startspannung in Volt")
        self._v_stop   = self._eingabezeile(tab, "V Stop (V):",             standard="50",
                                             tooltip="Sweep-Endspannung in Volt")
        self._punkte   = self._eingabezeile(tab, "Messpunkte:",             standard="1000",
                                             tooltip="Anzahl der Messpunkte (2 … 100.000)")
        self._trig_int = self._eingabezeile(tab, "Trigger-Intervall (µs):", standard="200",
                                             tooltip="Mindestzeit zwischen Triggern (min. 50 µs)")

    # ----------------------------------------------------------------------- #
    #  Tab 4: Instrument-Einstellungen
    # ----------------------------------------------------------------------- #

    def _tab_instrument(self, nb: ttk.Notebook) -> None:
        """Tab für hardware-spezifische SMU-Einstellungen."""
        tab = ttk.Frame(nb)
        nb.add(tab, text="Instrument")

        ttk.Label(tab, text="Instrument-Einstellungen",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        self._compliance = self._eingabezeile(
            tab, "Compliance (mA):", standard="100",
            tooltip="Maximaler Strom (Schutzgrenze) in Milliampere.\n"
                    "Das SMU schaltet den Ausgang ab wenn dieser Wert überschritten wird.")
        self._nplc = self._eingabezeile(
            tab, "NPLC:", standard="1.0",
            tooltip="Integrationszeit in Netzperioden (0,001 … 10).\n"
                    "Höher = weniger Rauschen, aber langsamerer Sweep.")

        ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        # High Capacitance Mode
        self._high_cap_var = tk.BooleanVar(value=False)
        f1 = ttk.Frame(tab); f1.pack(fill=tk.X, padx=8, pady=3)
        chk1 = ttk.Checkbutton(
            f1, text="High Capacitance Mode  (:SENS:CURR:HCAP ON)",
            variable=self._high_cap_var)
        chk1.pack(side=tk.LEFT)
        _tooltip(chk1,
                 "Aktivieren bei kapazitiver Last (lange Koaxkabel, große Sondenfläche).\n"
                 "Verhindert Schwingungen im Strommessverstärker.")

        # 4-Draht Remote Sensing
        self._remote_sensing_var = tk.BooleanVar(value=False)
        f2 = ttk.Frame(tab); f2.pack(fill=tk.X, padx=8, pady=3)
        chk2 = ttk.Checkbutton(
            f2, text="4-Draht Kelvin-Messung  (:SYST:RSEN ON)",
            variable=self._remote_sensing_var)
        chk2.pack(side=tk.LEFT)
        _tooltip(chk2,
                 "Kompensiert Leitungswiderstand — empfohlen für genaue I(V)-Messung.\n"
                 "ACHTUNG: Nicht verwenden wenn ein Multiplexer im Signalpfad liegt!")

    # ----------------------------------------------------------------------- #
    #  Tab 5: Physikalische Analyse
    # ----------------------------------------------------------------------- #

    def _tab_analyse(self, nb: ttk.Notebook) -> None:
        """Tab für die Parameter der physikalischen Auswertungs-Pipeline."""
        tab = ttk.Frame(nb)
        nb.add(tab, text="Analyse")

        ttk.Label(tab, text="Physikalische Analyse",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        self._ion_start  = self._eingabezeile(
            tab, "Ion-Fit Start (V):", standard="-50",
            tooltip="Linke Grenze des Ionensättigungsbereichs (muss < V_fl sein)")
        self._ion_stop   = self._eingabezeile(
            tab, "Ion-Fit Stop (V):",  standard="-30",
            tooltip="Rechte Grenze des Ionensättigungsbereichs (muss < V_fl sein)")
        self._filter_fenster = self._eingabezeile(
            tab, "Filter-Fenster:",    standard="21",
            tooltip="Fensterlänge des Glätters (ungerade Zahl, z. B. 21).\n"
                    "Faustregel: Fenster × dV ≤ T_e (in eV).\n"
                    "Bei 100 V / 1000 Punkte: dV = 0,1 V/Punkt → w=21 ergibt 2,1 V Breite.\n"
                    "Zu großes Fenster verschiebt V_p systematisch nach unten.")

        # Auswahl der Filtermethode
        fm_zeile = ttk.Frame(tab)
        fm_zeile.pack(fill=tk.X, padx=8, pady=(6, 2))
        ttk.Label(fm_zeile, text="Filter-Methode:", width=22, anchor="w").pack(side=tk.LEFT)
        self._filter_var = tk.StringVar(value="savgol")
        fm_combo = ttk.Combobox(
            fm_zeile, textvariable=self._filter_var,
            values=list(FILTER_METHODS), state="readonly", width=14)
        fm_combo.pack(side=tk.LEFT)
        _tooltip(fm_combo,
                 "savgol       – Savitzky-Golay (Standard, bewahrt Kurvenform + Ableitungen)\n"
                 "gaussian     – Gauß-Glättung (sanft, sigma = Fenster/6)\n"
                 "moving       – Gleitender Mittelwert (Reflect-Padding)\n"
                 "median       – Medianfilter (Reflect-Padding, robust gegen Spikes)\n"
                 "butterworth  – Butterworth-Tiefpass 4. Ordnung (nullphasig via filtfilt)\n"
                 "spike_savgol – Zweistufig: Median (Spike-Entfernung) + SavGol\n"
                 "none         – Keine Filterung (Rohdaten direkt verwenden)")

        ttk.Label(
            tab,
            text="Ion-Fit-Bereich sollte tief im Ionensättigungs-\n"
                 "plateau liegen (deutlich unterhalb von V_fl).",
            foreground="gray", font=("", 8), justify=tk.LEFT,
        ).pack(anchor="w", padx=8, pady=(8, 0))

    # ----------------------------------------------------------------------- #
    #  Tab 6: Ausgabe
    # ----------------------------------------------------------------------- #

    def _tab_ausgabe(self, nb: ttk.Notebook) -> None:
        """Tab für den CSV-Ausgabepfad."""
        tab = ttk.Frame(nb)
        nb.add(tab, text="Ausgabe")

        ttk.Label(tab, text="Ausgabe-Einstellungen",
                  font=("", 10, "bold")).pack(anchor="w", padx=8, pady=(10, 6))

        f = ttk.Frame(tab)
        f.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(f, text="Ausgabe-Ordner:", width=20, anchor="w").pack(side=tk.LEFT)
        self._ausgabe_ordner = ttk.Entry(f)
        self._ausgabe_ordner.insert(0, "messungen")
        self._ausgabe_ordner.pack(side=tk.LEFT, fill=tk.X, expand=True)
        durchsuchen_btn = ttk.Button(f, text="…", width=3,
                                     command=self._ausgabeordner_auswaehlen)
        durchsuchen_btn.pack(side=tk.LEFT, padx=(4, 0))
        _tooltip(durchsuchen_btn, "Zielordner für CSV-Dateien auswählen")

        ttk.Label(
            tab,
            text="CSV-Dateien werden mit Zeitstempel im\ngewählten Ordner gespeichert.",
            foreground="gray", font=("", 8), justify=tk.LEFT,
        ).pack(anchor="w", padx=8, pady=(8, 0))

    # ======================================================================= #
    #  Hilfsmethode – Eingabezeile mit Label
    # ======================================================================= #

    def _eingabezeile(
        self,
        elternteil: tk.Widget,
        beschriftung: str,
        standard: str = "",
        zustand: str = "normal",
        tooltip: str = "",
    ) -> ttk.Entry:
        """
        Erzeugt eine horizontale Zeile aus Label + Eingabefeld und gibt das Eingabefeld zurück.
        """
        zeile = ttk.Frame(elternteil)
        zeile.pack(fill=tk.X, padx=8, pady=3)
        lbl = ttk.Label(zeile, text=beschriftung, width=22, anchor="w")
        lbl.pack(side=tk.LEFT)
        eingabe = ttk.Entry(zeile, state=zustand)
        eingabe.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if standard:
            eingabe.insert(0, standard)
        if tooltip:
            _tooltip(eingabe, tooltip)
            _tooltip(lbl, tooltip)
        return eingabe

    # ======================================================================= #
    #  SMU-Ereignisbehandlung
    # ======================================================================= #

    def _simulation_umschalten(self) -> None:
        """Aktiviert/deaktiviert VISA-Adressfeld und Scan-Schaltfläche je nach Simulationsmodus."""
        simulation = self._simulation_var.get()
        self._smu_adresse_combo.configure(state="disabled" if simulation else "readonly")
        self._smu_scan_btn.configure(state="disabled" if simulation else "normal")

    def _ausgabeordner_auswaehlen(self) -> None:
        """Öffnet einen Ordner-Dialog und trägt den Pfad ins Eingabefeld ein."""
        ordner = filedialog.askdirectory(title="Ausgabe-Ordner wählen")
        if ordner:
            self._ausgabe_ordner.delete(0, tk.END)
            self._ausgabe_ordner.insert(0, ordner)

    def _smu_visa_scannen(self) -> None:
        """Startet den VISA-Scanner für das SMU im Hintergrund-Thread."""
        self._smu_scan_btn.configure(state="disabled")
        self._status_setzen("Suche SMU VISA-Ressourcen …")
        self._log("[VISA] Suche SMU-Ressourcen …\n", _TAG_INFO)
        threading.Thread(
            target=self._visa_scan_ausfuehren,
            args=(self._smu_adresse_combo, self._smu_scan_btn, "SMU"),
            daemon=True,
        ).start()

    # ======================================================================= #
    #  MUX-Ereignisbehandlung
    # ======================================================================= #

    def _mux_aktiv_umschalten(self) -> None:
        """Aktiviert oder deaktiviert alle MUX-Steuerelemente."""
        aktiv = self._mux_aktiv_var.get()
        # Eingabefelder
        for widget in (self._mux_slot, self._mux_abus,
                       self._mux_sonden_kanal, self._mux_manuell_kanal):
            widget.configure(state="normal" if aktiv else "disabled")
        # Adressfeld und Scanner
        self._mux_adresse_combo.configure(state="readonly" if aktiv else "disabled")
        self._mux_scan_btn.configure(state="normal" if aktiv else "disabled")
        # Verbinden-Schaltfläche
        self._mux_verbinden_btn.configure(state="normal" if aktiv else "disabled")
        # Schaltflächen für manuelle Steuerung nur aktiv wenn auch verbunden
        if not aktiv:
            self._mux_schliessen_btn.configure(state="disabled")
            self._mux_oeffnen_btn.configure(state="disabled")
            self._mux_alle_oeffnen_btn.configure(state="disabled")
            self._mux_status_var.set("MUX: deaktiviert")
            self._mux_treiber = None   # Verbindung verwerfen

    def _mux_visa_scannen(self) -> None:
        """Startet den VISA-Scanner für den MUX im Hintergrund-Thread."""
        self._mux_scan_btn.configure(state="disabled")
        self._status_setzen("Suche MUX VISA-Ressourcen …")
        self._log("[MUX] Suche MUX-Ressourcen …\n", _TAG_MUX)
        threading.Thread(
            target=self._visa_scan_ausfuehren,
            args=(self._mux_adresse_combo, self._mux_scan_btn, "MUX"),
            daemon=True,
        ).start()

    def _visa_scan_ausfuehren(
        self, combo: ttk.Combobox, schaltflaeche: ttk.Button, bezeichnung: str
    ) -> None:
        """Führt den VISA-Scan durch und aktualisiert die Combo-Box (Hintergrund-Thread)."""
        ressourcen = _visa_ressourcen_suchen()

        def _aktualisieren() -> None:
            if ressourcen:
                combo.configure(values=ressourcen)
                combo.set(ressourcen[0])
                self._log(f"[VISA] {bezeichnung}: {len(ressourcen)} Gerät(e) gefunden:\n",
                          _TAG_OK)
                for r in ressourcen:
                    self._log(f"   {r}\n", _TAG_OK)
            else:
                combo.configure(values=[])
                self._log(f"[VISA] {bezeichnung}: Keine Ressourcen gefunden.\n", _TAG_WARN)
            schaltflaeche.configure(state="normal")
            self._status_setzen("Bereit")

        self.after(0, _aktualisieren)

    def _mux_verbinden(self) -> None:
        """Schaltet zwischen Verbinden und Trennen des MUX um."""
        if self._mux_treiber is not None:
            # Bereits verbunden → trennen
            self._mux_trennen()
            return
        # Nicht verbunden → verbinden (im Hintergrund-Thread)
        threading.Thread(target=self._mux_verbindung_aufbauen, daemon=True).start()

    def _mux_verbindung_aufbauen(self) -> None:
        """Baut die VISA-Verbindung zum MUX auf (Hintergrund-Thread)."""
        adresse = self._mux_adresse_var.get().strip() or None
        try:
            slot = int(self._mux_slot.get().strip() or "1")
        except ValueError:
            slot = 1

        self.after(0, lambda: self._status_setzen("Verbinde MUX …"))
        self._log("[MUX] Verbinde …\n", _TAG_MUX)

        try:
            # pyvisa muss vorhanden sein
            if not _PYVISA_VORHANDEN:
                raise RuntimeError("pyvisa nicht installiert. Installieren mit: pip install pyvisa pyvisa-py")
            if adresse is None:
                raise RuntimeError("Keine VISA-Adresse für den MUX angegeben.")

            # VISA-Verbindung zum Mainframe öffnen
            rm   = _pyvisa.ResourceManager()
            inst = rm.open_resource(adresse)
            inst.timeout           = 5000   # 5 Sekunden Timeout
            inst.read_termination  = "\n"
            inst.write_termination = "\n"

            # Treiber-Objekt anlegen und Gerät identifizieren
            treiber = Mux34923ADriver(inst, slot=slot)
            idn     = treiber.identifizieren()

            # Treiber in der Instanz speichern und GUI aktualisieren
            self._mux_treiber = treiber
            self.after(0, lambda: self._mux_status_var.set(f"MUX: verbunden — {idn[:55]}"))
            self.after(0, lambda: self._mux_verbinden_btn.configure(text="✖ Trennen"))
            # Manuelle Steuerschaltflächen freischalten
            self.after(0, lambda: self._mux_schliessen_btn.configure(state="normal"))
            self.after(0, lambda: self._mux_oeffnen_btn.configure(state="normal"))
            self.after(0, lambda: self._mux_alle_oeffnen_btn.configure(state="normal"))
            self._log(f"[MUX] Verbunden: {idn}\n", _TAG_OK)
            self.after(0, lambda: self._status_setzen("MUX bereit"))

        except Exception as fehler:
            self._log(f"[MUX] Verbindungsfehler: {fehler}\n", _TAG_ERR)
            self.after(0, lambda: self._status_setzen("MUX-Verbindungsfehler", "red"))

    def _mux_trennen(self) -> None:
        """Trennt die MUX-Verbindung und setzt die GUI zurück."""
        if self._mux_treiber is not None:
            # Sicherheitshalber alle Kanäle öffnen bevor Verbindung getrennt wird
            try:
                self._mux_treiber.alle_oeffnen()
            except Exception:
                pass
            try:
                self._mux_treiber.schliessen()
            except Exception:
                pass
            self._mux_treiber = None
        # GUI zurücksetzen
        self._mux_verbinden_btn.configure(text="🔗 Verbinden")
        self._mux_schliessen_btn.configure(state="disabled")
        self._mux_oeffnen_btn.configure(state="disabled")
        self._mux_alle_oeffnen_btn.configure(state="disabled")
        self._mux_status_var.set("MUX: nicht verbunden")
        self._log("[MUX] Verbindung getrennt.\n", _TAG_MUX)

    def _mux_kanal_schliessen_manuell(self) -> None:
        """Schließt den manuell eingegebenen Kanal (Hintergrund-Thread)."""
        if self._mux_treiber is None:
            return
        kanal = self._mux_manuell_kanal.get().strip()
        if kanal:
            threading.Thread(
                target=self._mux_kanal_aktion,
                args=(kanal, "schliessen"),
                daemon=True,
            ).start()

    def _mux_kanal_oeffnen_manuell(self) -> None:
        """Öffnet den manuell eingegebenen Kanal (Hintergrund-Thread)."""
        if self._mux_treiber is None:
            return
        kanal = self._mux_manuell_kanal.get().strip()
        if kanal:
            threading.Thread(
                target=self._mux_kanal_aktion,
                args=(kanal, "oeffnen"),
                daemon=True,
            ).start()

    def _mux_kanal_aktion(self, kanal: str, aktion: str) -> None:
        """Führt eine Kanal-Schalten-Aktion im Hintergrund-Thread aus."""
        try:
            if aktion == "schliessen":
                self._mux_treiber.kanal_schliessen(kanal)
                self._log(f"[MUX] Kanal geschlossen: {kanal}\n", _TAG_MUX)
                self.after(0, lambda: self._mux_status_var.set(f"MUX: Kanal {kanal} geschlossen"))
            else:
                self._mux_treiber.kanal_oeffnen(kanal)
                self._log(f"[MUX] Kanal geöffnet: {kanal}\n", _TAG_MUX)
                self.after(0, lambda: self._mux_status_var.set(f"MUX: Kanal {kanal} geöffnet"))
        except Exception as fehler:
            self._log(f"[MUX] Fehler bei '{aktion}' Kanal {kanal}: {fehler}\n", _TAG_ERR)

    def _mux_alle_oeffnen(self) -> None:
        """Öffnet alle MUX-Kanäle (Hintergrund-Thread)."""
        if self._mux_treiber is None:
            return
        threading.Thread(target=self._mux_alle_oeffnen_ausfuehren, daemon=True).start()

    def _mux_alle_oeffnen_ausfuehren(self) -> None:
        """Sendet ROUT:OPEN:ALL an den MUX (Hintergrund-Thread)."""
        try:
            self._mux_treiber.alle_oeffnen()
            self._log("[MUX] Alle Kanäle geöffnet (ROUT:OPEN:ALL).\n", _TAG_MUX)
            self.after(0, lambda: self._mux_status_var.set("MUX: alle Kanäle geöffnet"))
        except Exception as fehler:
            self._log(f"[MUX] Fehler beim Öffnen aller Kanäle: {fehler}\n", _TAG_ERR)

    # ======================================================================= #
    #  Mess-Steuerung
    # ======================================================================= #

    def _messung_starten(self) -> None:
        """Startet den vollständigen Messablauf in einem Hintergrund-Thread."""
        if self._laeuft:
            return   # Mehrfach-Klick verhindern
        self._laeuft = True
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._plot_btn.configure(state="disabled")
        self._plot_speichern_btn.configure(state="disabled")
        self._plotter = None
        self._log_leeren()
        self._fortschritt.start(12)   # Animierten Fortschrittsbalken starten
        threading.Thread(target=self._messablauf, daemon=True).start()

    def _messung_stoppen(self) -> None:
        """Signalisiert Abbruch (Best-Effort – laufender Sweep kann nicht unterbrochen werden)."""
        self._log("[Stop] Abbruch angefordert. Laufender Sweep wird abgewartet.\n", _TAG_WARN)
        self._laeuft = False

    def _plot_anzeigen(self) -> None:
        """Zeigt das Ergebnis-Plotfenster an (oder hebt es in den Vordergrund)."""
        if self._plotter is None:
            return
        if self._plot_fenster is not None and self._plot_fenster.winfo_exists():
            self._plot_fenster.lift()   # Bereits geöffnet → in Vordergrund bringen
            return
        self._plotfenster_oeffnen()

    def _plot_speichern(self) -> None:
        """Öffnet einen Speichern-Dialog und exportiert die Abbildung."""
        if self._plotter is None:
            return
        pfad = filedialog.asksaveasfilename(
            title="Plot speichern",
            defaultextension=".png",
            filetypes=[
                ("PNG-Bild", "*.png"),
                ("PDF-Dokument", "*.pdf"),
                ("SVG-Vektorgrafik", "*.svg"),
            ],
        )
        if pfad:
            self._plotter.speichern(pfad)
            self._log(f"[Plot] Gespeichert: {pfad}\n", _TAG_OK)

    def _log_leeren(self) -> None:
        """Löscht den gesamten Inhalt des Log-Bereichs."""
        self._log_feld.configure(state="normal")
        self._log_feld.delete("1.0", tk.END)
        self._log_feld.configure(state="disabled")

    # ======================================================================= #
    #  Plot-Fenster
    # ======================================================================= #

    def _plotfenster_oeffnen(self) -> None:
        """Erzeugt ein neues Toplevel-Fenster mit der Matplotlib-Abbildung."""
        fenster = tk.Toplevel(self)
        fenster.title("Langmuirsonde  —  I(V)-Analyseergebnis")
        fenster.geometry("1100x780")
        self._plot_fenster = fenster

        # Matplotlib-Abbildung aufbauen und in tkinter einbetten
        fig    = self._plotter.build()
        leinwand = FigureCanvasTkAgg(fig, master=fenster)
        leinwand.draw()

        # Matplotlib-Navigationsleiste
        werkzeug_frame = ttk.Frame(fenster)
        werkzeug_frame.pack(side=tk.TOP, fill=tk.X)
        NavigationToolbar2Tk(leinwand, werkzeug_frame).update()

        leinwand.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # ======================================================================= #
    #  Thread-sichere Hilfsmethoden
    # ======================================================================= #

    def _log(self, text: str, tag: str = _TAG_INFO) -> None:
        """
        Fügt eine farbig markierte Zeile zum Log-Bereich hinzu.
        Thread-sicher durch self.after().
        """
        def _anhaengen() -> None:
            self._log_feld.configure(state="normal")
            self._log_feld.insert(tk.END, text, tag)
            self._log_feld.see(tk.END)      # Automatisch nach unten scrollen
            self._log_feld.configure(state="disabled")
        self.after(0, _anhaengen)

    def _status_setzen(self, text: str, farbe: str = "") -> None:
        """
        Aktualisiert den Text und optional die Farbe der Statusleiste.
        Thread-sicher durch self.after().
        """
        def _aktualisieren() -> None:
            self._status_var.set(text)
            self._status_lbl.configure(foreground=farbe if farbe else "")
        self.after(0, _aktualisieren)

    # ======================================================================= #
    #  Mess-Thread
    # ======================================================================= #

    def _messablauf(self) -> None:
        """Wrapper um _messablauf_ausfuehren – fängt unerwartete Fehler ab."""
        try:
            self._messablauf_ausfuehren()
        except Exception:
            tb = traceback.format_exc()
            self._log(f"\n[FEHLER] Unerwarteter Fehler:\n{tb}\n", _TAG_ERR)
            self._status_setzen("Fehler — Details im Protokoll", "red")
        finally:
            # GUI immer zurücksetzen
            self._laeuft = False
            self.after(0, self._fortschritt.stop)
            self.after(0, lambda: self._start_btn.configure(state="normal"))
            self.after(0, lambda: self._stop_btn.configure(state="disabled"))

    def _messablauf_ausfuehren(self) -> None:
        """
        Vollständiger Messablauf:
          1. Parameter lesen und validieren
          2. MUX ABus schließen
          3. MUX Sondenkanal schließen
          4. SMU verbinden und Sweep durchführen
          5. MUX Sondenkanal öffnen
          6. Rohdaten speichern
          7. Physikalische Analyse
          8. Ergebnisse speichern
          9. Plot anzeigen
        """
        # ── Parameter aus der GUI lesen ────────────────────────────────────
        try:
            v_start        = float(self._v_start.get())
            v_stop         = float(self._v_stop.get())
            n_punkte       = int(self._punkte.get())
            trig_intervall = float(self._trig_int.get()) * 1e-6   # µs → s
            compliance     = float(self._compliance.get()) / 1000.0   # mA → A
            nplc           = float(self._nplc.get())
            ion_start      = float(self._ion_start.get())
            ion_stop       = float(self._ion_stop.get())
            filter_fenster = int(self._filter_fenster.get())
            ausgabe_ordner = self._ausgabe_ordner.get().strip() or "messungen"
        except ValueError as fehler:
            self._log(f"[FEHLER] Ungültiger Parameter: {fehler}\n", _TAG_ERR)
            self._status_setzen("Fehler — ungültige Eingabe", "red")
            return

        filter_methode  = self._filter_var.get()
        simulation      = self._simulation_var.get()
        smu_adresse     = self._smu_adresse_var.get().strip() or None
        mux_aktiv       = self._mux_aktiv_var.get()
        abus_kanal      = self._mux_abus.get().strip()
        sonden_kanal    = self._mux_sonden_kanal.get().strip()
        auto_oeffnen    = self._mux_auto_oeffnen_var.get()

        # ── Protokoll-Header ───────────────────────────────────────────────
        self._log("=" * 55 + "\n", _TAG_KOPF)
        self._log("  Langmuirsonden-Messsystem  |  Keysight B2910BL\n", _TAG_KOPF)
        self._log("=" * 55 + "\n", _TAG_KOPF)
        self._log(f"  Filter: {filter_methode}   Fenster: {filter_fenster}\n", _TAG_INFO)
        if mux_aktiv:
            self._log(f"  MUX aktiv   ABus={abus_kanal or '—'}   Sonde={sonden_kanal}\n",
                      _TAG_MUX)
        self._log("=" * 55 + "\n", _TAG_KOPF)

        # ── Schritt 1 & 2 — MUX vorbereiten ───────────────────────────────
        if mux_aktiv:
            # MUX muss zuvor manuell verbunden worden sein
            if self._mux_treiber is None:
                self._log("[MUX] Kein MUX verbunden! Zuerst 'Verbinden' drücken.\n", _TAG_ERR)
                self._status_setzen("MUX nicht verbunden", "red")
                return

            # ABus-Bankbrücke schließen
            if abus_kanal:
                self._status_setzen(f"MUX: ABus-Kanal {abus_kanal} schließen …")
                self._log(f"[MUX] Schließe ABus-Kanal {abus_kanal} …\n", _TAG_MUX)
                try:
                    self._mux_treiber.kanal_schliessen(abus_kanal)
                    self._log(f"[MUX] ABus-Kanal {abus_kanal} geschlossen.\n", _TAG_MUX)
                except Exception as fehler:
                    self._log(f"[MUX] Fehler ABus: {fehler}\n", _TAG_ERR)
                    self._status_setzen("MUX-Fehler", "red")
                    return

            # Sondenkanal schließen
            if sonden_kanal:
                self._status_setzen(f"MUX: Sondenkanal {sonden_kanal} schließen …")
                self._log(f"[MUX] Schließe Sondenkanal {sonden_kanal} …\n", _TAG_MUX)
                try:
                    self._mux_treiber.kanal_schliessen(sonden_kanal)
                    self._log(f"[MUX] Sondenkanal {sonden_kanal} geschlossen.\n", _TAG_MUX)
                except Exception as fehler:
                    self._log(f"[MUX] Fehler Sondenkanal: {fehler}\n", _TAG_ERR)
                    self._status_setzen("MUX-Fehler", "red")
                    return

        # ── Schritt 3 — SMU verbinden ──────────────────────────────────────
        self._status_setzen("SMU verbinde …")
        verbindungsmanager = ConnectionManager(simulate=simulation)
        try:
            instrument = verbindungsmanager.connect(address=smu_adresse)
        except ConnectionError as fehler:
            self._log(f"\n[FEHLER] SMU-Verbindung fehlgeschlagen: {fehler}\n", _TAG_ERR)
            self._status_setzen("SMU-Verbindungsfehler", "red")
            return

        # ── Schritt 4 — SMU konfigurieren und Sweep durchführen ───────────
        smu_treiber = B2910BLDriver(
            instrument=instrument,
            compliance_current=compliance,
            nplc=nplc,
            high_cap_mode=self._high_cap_var.get(),
            remote_sensing=self._remote_sensing_var.get(),
        )

        V = I = None
        try:
            idn = smu_treiber.identify()
            self._log(f"\n[SMU] {idn}\n", _TAG_INFO)
            smu_treiber.reset()
            smu_treiber.configure()

            self._log(
                f"\n[Sweep]  {v_start:+.1f} V → {v_stop:+.1f} V  |  "
                f"{n_punkte} Punkte  |  NPLC={nplc}  |  "
                f"Compliance={compliance * 1e3:.0f} mA\n",
                _TAG_INFO,
            )
            # Aktivierte Sondereinstellungen ins Protokoll schreiben
            if self._high_cap_var.get():
                self._log("[SMU]  High Capacitance Mode: EIN\n", _TAG_WARN)
            if self._remote_sensing_var.get():
                self._log("[SMU]  4-Draht Remote Sensing: EIN\n", _TAG_INFO)

            self._status_setzen("Sweep läuft …")
            # Eigentlichen Spannungs-Sweep ausführen
            V, I = smu_treiber.run_sweep(
                v_start=v_start,
                v_stop=v_stop,
                n_points=n_punkte,
                trigger_interval=trig_intervall,
            )
            self._log(f"[Sweep] Abgeschlossen — {len(V)} Punkte erfasst.\n", _TAG_OK)

        except (ValueError, RuntimeError) as fehler:
            self._log(f"\n[FEHLER] Sweep fehlgeschlagen: {fehler}\n", _TAG_ERR)
            self._status_setzen("Sweep-Fehler", "red")
            verbindungsmanager.disconnect()
            # Auch bei Fehler den Sondenkanal öffnen (Sicherheit)
            if mux_aktiv and auto_oeffnen and sonden_kanal and self._mux_treiber:
                try:
                    self._mux_treiber.kanal_oeffnen(sonden_kanal)
                    self._log(f"[MUX] Sondenkanal {sonden_kanal} nach Fehler geöffnet.\n",
                              _TAG_MUX)
                except Exception:
                    pass
            return
        finally:
            # VISA-Verbindung zum SMU immer schließen
            verbindungsmanager.disconnect()

        # ── Schritt 5 — MUX Sondenkanal öffnen ────────────────────────────
        if mux_aktiv and auto_oeffnen and sonden_kanal and self._mux_treiber is not None:
            self._status_setzen(f"MUX: Sondenkanal {sonden_kanal} öffnen …")
            try:
                self._mux_treiber.kanal_oeffnen(sonden_kanal)
                self._log(f"[MUX] Sondenkanal {sonden_kanal} geöffnet.\n", _TAG_MUX)
            except Exception as fehler:
                self._log(f"[MUX] Warnung: Öffnen fehlgeschlagen: {fehler}\n", _TAG_WARN)

        # ── Schritt 6 — Rohdaten speichern ────────────────────────────────
        self._status_setzen("Speichere Rohdaten …")
        roh_pfad = save_raw_data(V, I, directory=ausgabe_ordner)
        self._log(f"[Export] Rohdaten: {roh_pfad}\n", _TAG_INFO)

        # ── Schritt 7 — Physikalische Analyse ──────────────────────────────
        self._status_setzen("Physikalische Analyse …")
        self._log("\n[Analyse] Starte Analyse-Pipeline …\n", _TAG_INFO)
        self._log(f"[Analyse] Filter: {filter_methode}  Fenster: {filter_fenster}\n",
                  _TAG_INFO)

        analyser = LangmuirAnalyzer(
            V=V, I=I,
            savgol_window=filter_fenster,
            ion_fit_range=(ion_start, ion_stop),
            filter_method=filter_methode,
        )
        try:
            ergebnisse = analyser.analyze()
        except AnalysisError as fehler:
            self._log(f"\n[FEHLER] Analyse fehlgeschlagen: {fehler}\n", _TAG_ERR)
            self._log(
                "Tipp: Ion-Fit-Bereich (Tab 'Analyse') oder Sweep-Bereich anpassen.\n",
                _TAG_WARN,
            )
            self._status_setzen("Analyse-Fehler", "red")
            return

        # Zusammenfassung der Plasmaparameter ins Protokoll schreiben
        self._log(ergebnisse.summary() + "\n", _TAG_OK)

        # ── Schritt 8 — Ergebnisse speichern ──────────────────────────────
        sweep_parameter = {
            "v_start":       v_start,
            "v_stop":        v_stop,
            "n_punkte":      n_punkte,
            "compliance_A":  compliance,
            "nplc":          nplc,
            "filter_methode": filter_methode,
            "filter_fenster": filter_fenster,
            "high_cap":      self._high_cap_var.get(),
            "remote_sensing": self._remote_sensing_var.get(),
            "mux_aktiv":     mux_aktiv,
            "mux_abus":      abus_kanal,
            "mux_sonde":     sonden_kanal,
        }
        erg_pfad = save_results(ergebnisse, directory=ausgabe_ordner,
                                sweep_params=sweep_parameter)
        self._log(f"[Export] Ergebnisse: {erg_pfad}\n", _TAG_INFO)

        # ── Schritt 9 — Plot vorbereiten und anzeigen ──────────────────────
        self._plotter = LangmuirPlotter(V, I, analyser, ergebnisse)
        self._log("\n[Fertig] Alle Schritte erfolgreich abgeschlossen.\n", _TAG_OK)
        self._status_setzen("Fertig  —  Plot bereit", "green")

        # Schaltflächen für Plot freischalten und Fenster automatisch öffnen
        self.after(0, lambda: self._plot_btn.configure(state="normal"))
        self.after(0, lambda: self._plot_speichern_btn.configure(state="normal"))
        self.after(0, self._plotfenster_oeffnen)


# ============================================================================ #
#  Programmstart
# ============================================================================ #

if __name__ == "__main__":
    app = LangmuirGUI()
    app.mainloop()
