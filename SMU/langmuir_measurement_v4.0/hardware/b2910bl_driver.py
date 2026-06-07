"""
Treiber für das Keysight B2910BL Source-Measure Unit (SMU)
===========================================================
Vollständiger SCPI-Treiber für das Keysight B2910BL 1-Kanal Präzisions-
Source-Measure-Unit. Jeder SCPI-Befehl ist mit seiner physikalischen
Bedeutung kommentiert.

Gerätegrenzen (aus dem Datenblatt):
    - ±210 V, ±1,5 A DC
    - Nur DC-Betrieb (BL-Variante unterstützt KEINEN Pulsbetrieb)
    - 10 fA Stromauflösung (wird als Rauschgrenze verwendet)
    - Minimales Trigger-Intervall: 50 µs
    - Interner Trace-Puffer: bis zu 100.000 Punkte
    - High Capacitance Mode verfügbar (:SENS:CURR:HCAP ON)
    - Einstellbares 4-Draht / 2-Draht Remote Sensing
"""

from __future__ import annotations

import time
import numpy as np
from typing import Tuple


class B2910BLDriver:
    """
    SCPI-Treiber für das Keysight B2910BL Source Measure Unit.

    Diese Klasse ist rein hardware-seitig: Sie konfiguriert das Gerät,
    führt einen DC-Spannungs-Sweep durch und gibt rohe (V, I)-Arrays zurück.
    Physikalische Auswertung gehört nicht hierher – siehe physics/langmuir_analysis.py.

    Parameter
    ---------
    instrument :
        Geöffnete VISA-Ressource (pyvisa.Resource oder MockInstrument).
    compliance_current : float
        Maximaler Strom, den das SMU liefern/aufnehmen darf (A).
        Standard 0,1 A (100 mA) schützt empfindliche Plasmasonden.
    nplc : float
        Integrationszeit in Netzperioden (Power Line Cycles).
        1 PLC ≈ 16,67 ms bei 60 Hz. Bereich: 0,001 – 10.
        Höher = weniger Rauschen, langsamerer Sweep.
    high_cap_mode : bool
        Aktiviert den High Capacitance Mode (:SENS:CURR:HCAP ON).
        Verwenden bei kapazitiven Lasten (lange Koaxkabel, große Sondenflächen),
        um Schwingungen im Strommessverstärker zu verhindern.
    remote_sensing : bool
        True  → 4-Draht Kelvin-Messung (:SYST:RSEN ON) – eliminiert
                 Leitungswiderstandsfehler; bevorzugt für genaue I(V)-Messung.
        False → 2-Draht lokales Sensing – erforderlich bei Verwendung eines
                 Multiplexers oder Relaismatrix zwischen SMU und Sonde.
    """

    # Hardwaregrenzen des B2910BL (aus dem Datenblatt fest einprogrammiert)
    VOLT_MAX: float        = 210.0   # V   – maximaler Spannungsbereich
    CURR_MAX: float        = 1.5     # A   – maximaler Strombereich
    TRIG_INTERVAL_MIN: float = 50e-6 # s   – minimaler Trigger-Abstand (50 µs)
    BUFFER_MAX: int        = 100_000 # Pkt – maximale Puffergröße

    def __init__(
        self,
        instrument,
        compliance_current: float = 0.1,
        nplc: float = 1.0,
        high_cap_mode: bool = False,
        remote_sensing: bool = False,
    ) -> None:
        self.inst               = instrument
        self.compliance_current = compliance_current
        self.nplc               = nplc
        self.high_cap_mode      = high_cap_mode
        self.remote_sensing     = remote_sensing

    # ================================================================== #
    #  Öffentliche Schnittstelle
    # ================================================================== #

    def identifizieren(self) -> str:
        """Gibt den *IDN?-Identifikationsstring des Geräts zurück."""
        return self.inst.query("*IDN?").strip()

    # Alias für die GUI, die noch den englischen Namen verwendet
    def identify(self) -> str:
        """Alias für identifizieren() – Rückwärtskompatibilität."""
        return self.identifizieren()

    def zuruecksetzen(self) -> None:
        """
        Werkseinstellungen wiederherstellen und Fehlerwarteschlange leeren.

        Sollte vor jeder neuen Messsitzung aufgerufen werden, damit keine
        Einstellungen aus einem vorangegangenen Lauf das Ergebnis verfälschen.
        """
        self.inst.write("*RST")   # Werksreset – alle Benutzereinstellungen werden gelöscht
        self.inst.write("*CLS")   # Statusbyte und alle Ereignisregister löschen
        time.sleep(0.5)           # Firmware-Zeit für den Reset abwarten

    # Alias für die GUI
    def reset(self) -> None:
        """Alias für zuruecksetzen() – Rückwärtskompatibilität."""
        self.zuruecksetzen()

    def konfigurieren(self) -> None:
        """
        Alle statischen Geräteeinstellungen setzen.

        Die Trennung von Konfiguration und Ausführung ermöglicht eine
        Überprüfung der Einstellungen (z. B. mit :SYST:ERR?) bevor
        der eigentliche Sweep gestartet wird.
        """
        self._quelle_konfigurieren()
        self._messung_konfigurieren()
        self._system_konfigurieren()

    # Alias für die GUI
    def configure(self) -> None:
        """Alias für konfigurieren() – Rückwärtskompatibilität."""
        self.konfigurieren()

    def sweep_ausfuehren(
        self,
        v_start: float,
        v_stop: float,
        n_punkte: int,
        trigger_intervall: float = 200e-6,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        DC-Spannungs-Sweep durchführen und (V, I)-Arrays zurückgeben.

        Das SMU rampt die Spannung linear von v_start nach v_stop und
        misst an jedem Schritt den Strom. Die Daten werden nach Abschluss
        aus dem internen Gerätepuffer ausgelesen.

        Parameter
        ---------
        v_start : float
            Sweep-Startspannung (V). |v_start| ≤ 210 V.
        v_stop : float
            Sweep-Endspannung (V). |v_stop| ≤ 210 V.
        n_punkte : int
            Anzahl der Messpunkte. 2 ≤ n_punkte ≤ 100.000.
        trigger_intervall : float
            Mindestzeit zwischen aufeinanderfolgenden Triggern (s).
            Muss ≥ 50 µs (B2910BL Hardware-Minimum) betragen.
            Standard 200 µs liefert stabile Messungen bei typischen Sonden.

        Rückgabe
        --------
        V : np.ndarray   Spannungsarray (V), Form (n_punkte,)
        I : np.ndarray   Stromarray (A), Form (n_punkte,)

        Ausnahmen
        ---------
        ValueError   Bei Parametern außerhalb des zulässigen Bereichs.
        RuntimeError Falls das Gerät eine unerwartete Datenlänge zurückgibt.
        """
        # Parameter prüfen bevor Hardware angefasst wird
        self._sweep_parameter_pruefen(v_start, v_stop, n_punkte, trigger_intervall)
        # Sweep-Grenzen und Punktzahl ins Gerät übertragen
        self._sweep_quelle_setzen(v_start, v_stop, n_punkte)
        # Trigger-System konfigurieren
        self._sweep_trigger_setzen(n_punkte, trigger_intervall)

        try:
            self.inst.write(":OUTP ON")   # SMU-Ausgang aktivieren
            self.inst.write(":INIT")      # Trigger armen und Sweep starten
            self._auf_abschluss_warten()  # Blockieren bis Sweep fertig
            V, I = self._daten_auslesen(n_punkte)
        finally:
            # Ausgang IMMER deaktivieren – sicherheitskritisch
            self.inst.write(":OUTP OFF")

        return V, I

    # Alias für die GUI
    def run_sweep(
        self,
        v_start: float,
        v_stop: float,
        n_points: int,
        trigger_interval: float = 200e-6,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Alias für sweep_ausfuehren() – Rückwärtskompatibilität."""
        return self.sweep_ausfuehren(v_start, v_stop, n_points, trigger_interval)

    # ================================================================== #
    #  Interne Hilfsmethoden – statische Konfiguration
    # ================================================================== #

    def _quelle_konfigurieren(self) -> None:
        """Spannungsquelle für DC-Linearsweep-Betrieb konfigurieren."""
        self.inst.write(":SOUR:FUNC:MODE VOLT")  # Spannungsquelle (nicht Strom)
        self.inst.write(":SOUR:VOLT:MODE SWE")   # Sweep-Modus (statt Fix oder Liste)
        self.inst.write(":SOUR:SWE:SPAC LIN")    # Linearer (gleichmäßiger) Schrittabstand
        self.inst.write(":SOUR:SWE:RANG BEST")   # Besten Spannungsbereich automatisch wählen
        self.inst.write(":SOUR:DEL:AUTO ON")      # Automatische Quell-Einschwingverzögerung

    def _messung_konfigurieren(self) -> None:
        """Strommessung, Compliance-Grenze und Integrationszeit konfigurieren."""
        self.inst.write(':SENS:FUNC "CURR"')                         # Strommessung (nicht Spannung oder Widerstand)
        self.inst.write(":SENS:CURR:RANG:AUTO ON")                    # Automatische Messbereichswahl
        self.inst.write(f":SENS:CURR:NPLC {self.nplc:.4g}")           # Integrationszeit: 1 PLC = 16,67 ms
        self.inst.write(f":SENS:CURR:PROT {self.compliance_current:.6g}")
        # Strom-Compliance: SMU schaltet Ausgang ab wenn dieser Wert überschritten wird.
        # Standard 100 mA schützt Sondendrähte und Plasmagleichgewicht.

        # High Capacitance Mode: reduziert Messbandbreite, verhindert Schwingungen
        # bei kapazitiven Lasten (Koaxkabel, große Sondenflächen > ~10 nF).
        hcap = "ON" if self.high_cap_mode else "OFF"
        self.inst.write(f":SENS:CURR:HCAP {hcap}")

    def _system_konfigurieren(self) -> None:
        """Systemeinstellungen: Remote Sensing und Datenformat."""
        # Remote Sensing EIN  → Kelvin 4-Draht: SMU kompensiert Leitungswiderstand.
        # Remote Sensing AUS  → 2-Draht: erforderlich wenn Relais/MUX im Signalpfad liegt.
        rsen = "ON" if self.remote_sensing else "OFF"
        self.inst.write(f":SYST:RSEN {rsen}")

        # Nur Spannung + Strom pro Messpunkt anfordern.
        # Widerstand, Zeitstempel und Statuswort weglassen → halbe Übertragungszeit.
        self.inst.write(":FORM:ELEM:SENS VOLT,CURR")

    # ================================================================== #
    #  Interne Hilfsmethoden – Sweep-Ausführung
    # ================================================================== #

    def _sweep_quelle_setzen(self, v_start: float, v_stop: float, n_punkte: int) -> None:
        """Sweep-Grenzen und Punktzahl in das Quell-Subsystem schreiben."""
        self.inst.write(f":SOUR:VOLT:STAR {v_start:.6g}")  # Erster Spannungsschritt
        self.inst.write(f":SOUR:VOLT:STOP {v_stop:.6g}")   # Letzter Spannungsschritt
        self.inst.write(f":SOUR:VOLT:POIN {n_punkte:d}")   # Gesamtanzahl der Schritte

    def _sweep_trigger_setzen(self, n_punkte: int, trigger_intervall: float) -> None:
        """Trigger-Subsystem für vollautomatische Sweep-Aufnahme konfigurieren."""
        self.inst.write(":TRIG:SOUR AINT")                          # Automatischer interner Trigger
        self.inst.write(f":TRIG:DEL {trigger_intervall:.6g}")        # Verzögerung ≥ 50 µs zwischen Triggern
        self.inst.write(f":TRIG:ALL:COUN {n_punkte:d}")              # Quelle + Messung synchronisieren

    def _auf_abschluss_warten(self, timeout_s: float = 600.0) -> None:
        """
        Blockiert bis das SMU *OPC (Operation Complete) meldet.

        *OPC? gibt '1' zurück, sobald alle ausstehenden Operationen abgeschlossen sind.
        Zuverlässiger als eine feste Wartezeit und behandelt variable Sweep-Dauern korrekt.
        """
        alter_timeout = self.inst.timeout
        # pyvisa erwartet den Timeout in Millisekunden
        self.inst.timeout = int(timeout_s * 1000)
        try:
            # Blockiert bis Sweep + Datenübertragung abgeschlossen sind
            self.inst.query("*OPC?")
        finally:
            # Ursprünglichen Timeout wiederherstellen
            self.inst.timeout = alter_timeout

    def _daten_auslesen(self, n_punkte: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        Messdaten aus dem internen Gerätepuffer auslesen.

        :FETC:ARR? gibt alle gepufferten Daten als kommagetrennte ASCII-Zeichenkette zurück.
        Mit :FORM:ELEM:SENS VOLT,CURR ist das Format verschachtelt:
            V₁, I₁, V₂, I₂, … Vₙ, Iₙ

        Hinweis: :FETC? (ohne ARR) gibt nur den letzten Skalarpunkt zurück – falsch!
        """
        # Alle Punkte als ASCII-String abrufen
        roh = self.inst.query(":FETC:ARR?")
        werte = np.fromstring(roh, dtype=float, sep=",")

        # Mindestens ein V-I-Paar muss vorhanden sein
        if len(werte) < 2:
            raise RuntimeError(
                f":FETC:ARR? hat nur {len(werte)} Wert(e) zurückgegeben. "
                "Erwartet: mindestens 2 (ein V-I-Paar)."
            )

        # Verschachtelte V-I-Paare entflechten
        V = werte[0::2][:n_punkte]  # geradzahlige Indizes = Spannung
        I = werte[1::2][:n_punkte]  # ungeradzahlige Indizes = Strom
        return V, I

    # ================================================================== #
    #  Interne Hilfsmethoden – Parameterpüfung
    # ================================================================== #

    def _sweep_parameter_pruefen(
        self,
        v_start: float,
        v_stop: float,
        n_punkte: int,
        trig_intervall: float,
    ) -> None:
        """
        Wirft ValueError wenn ein Parameter außerhalb des zulässigen Bereichs liegt.
        Wird vor dem ersten Hardware-Zugriff aufgerufen.
        """
        if abs(v_start) > self.VOLT_MAX:
            raise ValueError(
                f"v_start={v_start} V überschreitet das B2910BL-Limit von ±{self.VOLT_MAX} V"
            )
        if abs(v_stop) > self.VOLT_MAX:
            raise ValueError(
                f"v_stop={v_stop} V überschreitet das B2910BL-Limit von ±{self.VOLT_MAX} V"
            )
        if not (2 <= n_punkte <= self.BUFFER_MAX):
            raise ValueError(
                f"n_punkte={n_punkte} liegt außerhalb des zulässigen Bereichs [2, {self.BUFFER_MAX}]"
            )
        if trig_intervall < self.TRIG_INTERVAL_MIN:
            raise ValueError(
                f"trigger_intervall={trig_intervall * 1e6:.1f} µs unterschreitet das "
                f"B2910BL-Minimum von {self.TRIG_INTERVAL_MIN * 1e6:.0f} µs"
            )
        if self.compliance_current > self.CURR_MAX:
            raise ValueError(
                f"compliance_current={self.compliance_current} A überschreitet das "
                f"B2910BL-Maximum von {self.CURR_MAX} A"
            )
