"""
Treiber für den Keysight 34923A Multiplexer
============================================
SCPI-Treiber für die 34923A Armaturenmultiplexer-Karte (40-Kanal),
eingebaut in ein 34980A- oder 34970A-Mainframe.

Karten-Layout (zwei Bänke, je 1×20):
  Bank 1 : Kanäle 1..20
  Bank 2 : Kanäle 21..40

Kanalformat im Mainframe (Beispiel Slot 1, Kanal 1): 1001
  → vier Ziffern: [Slot][Bank][Kanal zweistellig]
  Beispiele:
    1001 = Slot 1, Kanal  1
    1020 = Slot 1, Kanal 20
    1021 = Slot 1, Kanal 21 (Bank 2)

Verwendete SCPI-Befehle:
  *IDN?              – Geräteidentifikation
  *RST               – Werksreset
  *CLS               – Statusregister/Fehlerwarteschlange löschen
  ROUT:CLOS (@x)     – Kanal(e) schließen (verbinden)
  ROUT:OPEN (@x)     – Kanal(e) öffnen (trennen)
  ROUT:OPEN:ALL      – Alle Kanäle öffnen
  ROUT:CLOS:STAT? (@x) – Schaltzustand abfragen (0=offen, 1=geschlossen)

Literatur:
  Keysight 34980A Multifunction Switch/Measure Unit User Guide
  Keysight 34923A 40/80-Channel Armature MUX Module User Guide
"""

from __future__ import annotations

import time


class Mux34923ADriver:
    """
    SCPI-Treiber für eine Keysight 34923A Multiplexer-Karte.

    Parameter
    ---------
    instrument :
        Geöffnete pyvisa-Ressource (muss .write(), .query(), .close() unterstützen).
    slot : int
        Slot-Nummer im Mainframe, in dem die 34923A steckt (Standard: 1).
    verzoegerung : float
        Wartezeit in Sekunden nach jedem Schalt-Befehl für Relais-Einschwingzeit
        (Standard: 0.1 s = 100 ms).
    """

    def __init__(
        self,
        instrument,
        slot: int = 1,
        verzoegerung: float = 0.1,
    ) -> None:
        self.inst        = instrument
        self.slot        = slot
        self.verzoegerung = verzoegerung

    # ================================================================== #
    #  Öffentliche Schnittstelle
    # ================================================================== #

    def identifizieren(self) -> str:
        """Gibt den *IDN?-Identifikationsstring des Mainframes zurück."""
        return self.inst.query("*IDN?").strip()

    def zuruecksetzen(self) -> None:
        """
        Mainframe auf Werkseinstellungen zurücksetzen und Fehlerwarteschlange leeren.
        Sollte einmalig zu Beginn einer Messsitzung aufgerufen werden.
        """
        self.inst.write("*RST")   # Werksreset – alle Benutzereinstellungen gelöscht
        self.inst.write("*CLS")   # Statusregister und Fehlerwarteschlange leeren
        time.sleep(0.5)           # Firmware Zeit zum Abschließen des Resets geben

    def alle_oeffnen(self) -> None:
        """
        Alle Relais der gesamten Karte öffnen (trennen).
        Entspricht dem SCPI-Befehl ROUT:OPEN:ALL.
        """
        self.inst.write("ROUT:OPEN:ALL")
        time.sleep(self.verzoegerung)

    def kanal_schliessen(self, kanalliste: str, verzoegerung: float | None = None) -> None:
        """
        Einen oder mehrere Kanäle schließen (verbinden).

        Parameter
        ---------
        kanalliste : str
            Ein oder mehrere Kanalbezeichnungen als kommagetrennte Zeichenkette,
            z. B. "1001" oder "1001,1002,1011".
            Das Format entspricht dem, was direkt im Mainframe erwartet wird
            (Slot+Bank+Kanal, z. B. 1001 = Slot 1, Kanal 1).
        verzoegerung : float, optional
            Überschreibt die Standard-Wartezeit nach dem Schalten.
        """
        # Kanalliste für SCPI-Befehl aufbereiten
        formatiert = self._kanalliste_formatieren(kanalliste)
        self.inst.write(f"ROUT:CLOS (@{formatiert})")
        # Wartezeit für Relais-Einschwingzeit einhalten
        time.sleep(verzoegerung if verzoegerung is not None else self.verzoegerung)

    def kanal_oeffnen(self, kanalliste: str, verzoegerung: float | None = None) -> None:
        """
        Einen oder mehrere Kanäle öffnen (trennen).

        Parameter
        ---------
        kanalliste : str
            Gleiche Format wie bei kanal_schliessen().
        verzoegerung : float, optional
            Überschreibt die Standard-Wartezeit nach dem Schalten.
        """
        formatiert = self._kanalliste_formatieren(kanalliste)
        self.inst.write(f"ROUT:OPEN (@{formatiert})")
        time.sleep(verzoegerung if verzoegerung is not None else self.verzoegerung)

    def schaltzustand_abfragen(self, kanalliste: str) -> dict[str, bool]:
        """
        Fragt den Schaltzustand jedes Kanals in der Liste ab.

        Rückgabe
        --------
        dict  Kanal-String → bool (True = geschlossen/verbunden).
        """
        # Kanalliste aufteilen und einzeln abfragen
        kanaele = [k.strip() for k in kanalliste.split(",") if k.strip()]
        ergebnis: dict[str, bool] = {}
        for kanal in kanaele:
            formatiert = self._kanalliste_formatieren(kanal)
            try:
                antwort = self.inst.query(f"ROUT:CLOS:STAT? (@{formatiert})").strip()
                # Antwort "1" bedeutet geschlossen, "0" bedeutet offen
                ergebnis[kanal] = antwort.startswith("1")
            except Exception:
                # Bei Kommunikationsfehler: Kanal als offen annehmen
                ergebnis[kanal] = False
        return ergebnis

    def befehl_senden(self, befehl: str) -> None:
        """Sendet einen beliebigen SCPI-Befehl (keine Antwort erwartet)."""
        self.inst.write(befehl)

    def abfrage_senden(self, befehl: str) -> str:
        """Sendet eine beliebige SCPI-Abfrage und gibt die Antwort zurück."""
        return self.inst.query(befehl).strip()

    def schliessen(self) -> None:
        """VISA-Verbindung zum Mainframe trennen."""
        try:
            self.inst.close()
        except Exception:
            pass  # Verbindung bereits getrennt – kein Fehler

    # ================================================================== #
    #  Interne Hilfsmethoden
    # ================================================================== #

    @staticmethod
    def _kanalliste_formatieren(roh: str) -> str:
        """
        Normalisiert eine vom Benutzer eingegebene Kanalliste für den SCPI-Befehl.

        Akzeptiert kommagetrennte Einträge wie "1001,1002" oder "1001" und gibt
        sie ohne Leerzeichen zusammengefügt zurück, bereit für (@...).

        Beispiel: "1001, 1002" → "1001,1002"
        """
        # Leerzeichen entfernen und leere Einträge überspringen
        kanaele = [k.strip() for k in roh.split(",") if k.strip()]
        return ",".join(kanaele)
