"""
Verbindungsmanager – VISA-Verbindung mit Simulations-Fallback
=============================================================
Implementiert drei Verbindungsstrategien in dieser Reihenfolge:

  1. Explizite Adresse  – Aufrufer gibt direkt einen VISA-String an.
  2. Automatische Erkennung – Scannt pyvisa.ResourceManager().list_resources()
     und erkennt wahrscheinliche B2910B/BL-Geräte anhand von USB-VID/PID.
  3. Manuelle Eingabe – Konsolenprompt falls die automatische Erkennung scheitert.
  4. Simulation – Erzeugt synthetische Langmuir-I(V)-Daten; keine Hardware erforderlich.
     Wird mit simulate=True oder ohne installierten VISA-Treiber aktiviert.

Der Simulationsmodus ist nützlich für:
  - Offline-Algorithmusentwicklung und Tests
  - Demo des Analyse-Pipelines ohne Laborausrüstung
  - Automatisierte Regressionstests

VISA-Adress-Formate:
  USB0::0x0957::0x8B18::MY12345678::INSTR   (USB, typisch für B2910BL)
  GPIB0::23::INSTR                           (GPIB)
  TCPIP0::192.168.1.10::inst0::INSTR         (LAN/VXI-11)
"""

from __future__ import annotations

import numpy as np
from typing import Optional


# Keysight B2910BL USB-Identifikatoren (aus dem USB-Deskriptor)
_KEYSIGHT_VID = "0x0957"   # Hersteller-ID (Keysight / Agilent)
_B2900_PID    = "0x8B18"   # Produkt-ID der B2900-Serie

# Physikalische Parameter des synthetischen I(V)-Kurven-Simulators
_SIM_V_SCHWEBE    = 2.0     # V   – Schwebepotential
_SIM_V_PLASMA     = 10.0    # V   – Plasmapotential
_SIM_T_E          = 3.0     # eV  – Elektronentemperatur
_SIM_I_ION_SAT    = -5e-3   # A   – Ionensättigungsstrom (negativ)
_SIM_I_E_SAT      = 50e-3   # A   – Elektronensättigungsstrom (positiv)
_SIM_RAUSCHANTEIL = 0.02    # –   – Gauß'sches Rauschen als Bruchteil von |I_ion_sat|


class ConnectionManager:
    """
    Verwaltet den Lebenszyklus einer VISA-Gerätverbindung.

    Parameter
    ---------
    simulate : bool
        Wenn True, wird die gesamte Hardware übersprungen und stattdessen
        ein MockInstrument (Simulator) verwendet.
    visa_backend : str
        pyvisa-Backend-String, z. B. '' (NI-VISA) oder '@py' (pyvisa-py).
        Wird bei simulate=True ignoriert.
    """

    def __init__(self, simulate: bool = False, visa_backend: str = "") -> None:
        self.simulate     = simulate
        self.visa_backend = visa_backend
        self._rm          = None   # pyvisa ResourceManager
        self._instrument  = None   # geöffnetes VISA-Instrument

    # ================================================================== #
    #  Öffentliche Schnittstelle
    # ================================================================== #

    def connect(self, address: Optional[str] = None):
        """
        Verbindung öffnen und Instrumentenhandle zurückgeben.

        Parameter
        ---------
        address : str, optional
            VISA-Ressourcen-String. Bei None wird zuerst automatisch
            gesucht, dann per Konsolenprompt nachgefragt.

        Rückgabe
        --------
        Instrumentenhandle (pyvisa.Resource oder MockInstrument).
        Das zurückgegebene Objekt implementiert immer .write(), .query(), .close().
        """
        # Simulationsmodus: echte Hardware komplett überspringen
        if self.simulate:
            print("[Verbindung] Simulationsmodus – keine Hardware verwendet.")
            self._instrument = MockInstrument()
            return self._instrument

        # Prüfen ob pyvisa installiert ist
        try:
            import pyvisa  # type: ignore
        except ImportError:
            print(
                "[Verbindung] WARNUNG: pyvisa nicht installiert. "
                "Falle auf Simulationsmodus zurück.\n"
                "Installieren mit:  pip install pyvisa pyvisa-py"
            )
            self._instrument = MockInstrument()
            return self._instrument

        # pyvisa ResourceManager öffnen
        self._rm = pyvisa.ResourceManager(self.visa_backend)

        # Adresse bestimmen: explizit angegeben, automatisch erkannt oder manuell eingegeben
        if address is not None:
            aufgeloest = address
        else:
            aufgeloest = self._automatisch_erkennen() or self._manuell_eingeben()

        # Keine Adresse gefunden
        if aufgeloest is None:
            raise ConnectionError(
                "Keine VISA-Adresse verfügbar. "
                "Adresse angeben oder Simulationsmodus verwenden."
            )

        print(f"[Verbindung] Verbinde mit: {aufgeloest}")
        self._instrument = self._rm.open_resource(aufgeloest)
        self._instrument.timeout           = 30_000  # 30 s Standard-Timeout; wird beim Sweep überschrieben
        self._instrument.read_termination  = "\n"
        self._instrument.write_termination = "\n"
        return self._instrument

    def disconnect(self) -> None:
        """VISA-Verbindung sauber schließen."""
        if self._instrument is not None:
            try:
                self._instrument.close()
            except Exception:
                pass
            self._instrument = None
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:
                pass
            self._rm = None

    # ================================================================== #
    #  Interne Verbindungsstrategien
    # ================================================================== #

    def _automatisch_erkennen(self) -> Optional[str]:
        """
        Alle VISA-Ressourcen nach einem wahrscheinlichen B2910BL durchsuchen.

        Sucht Ressourcen, die die Keysight-VID (0x0957) oder die
        B2900-Serie-PID (0x8B18) im Ressourcen-String enthalten.
        """
        try:
            ressourcen = self._rm.list_resources()
        except Exception as fehler:
            print(f"[Verbindung] list_resources() fehlgeschlagen: {fehler}")
            return None

        # Kandidaten filtern
        kandidaten = [
            r for r in ressourcen
            if _KEYSIGHT_VID in r.upper() or _B2900_PID in r.upper()
            or "B2910" in r.upper() or "B2900" in r.upper()
        ]

        if len(kandidaten) == 1:
            print(f"[Verbindung] Automatisch erkannt: {kandidaten[0]}")
            return kandidaten[0]

        if len(kandidaten) > 1:
            # Mehrere Kandidaten: Benutzer auswählen lassen
            print("[Verbindung] Mehrere B2910BL-Kandidaten gefunden:")
            for i, r in enumerate(kandidaten):
                print(f"  [{i}] {r}")
            while True:
                auswahl = input("Index auswählen: ").strip()
                if auswahl.isdigit() and int(auswahl) < len(kandidaten):
                    return kandidaten[int(auswahl)]
                print("Ungültige Auswahl – bitte erneut versuchen.")

        # Kein B2910BL gefunden – alle verfügbaren Ressourcen zur Info anzeigen
        if ressourcen:
            print("[Verbindung] Verfügbare VISA-Ressourcen (kein B2910BL erkannt):")
            for r in ressourcen:
                print(f"  {r}")
        else:
            print("[Verbindung] Keine VISA-Ressourcen gefunden.")

        return None

    def _manuell_eingeben(self) -> Optional[str]:
        """Fallback: Benutzer gibt die VISA-Adresse auf der Konsole ein."""
        print("\n[Verbindung] Manuelle VISA-Adress-Eingabe erforderlich.")
        print("Beispiel: USB0::0x0957::0x8B18::MY12345678::INSTR")
        adresse = input("VISA-Adresse eingeben (Enter für Simulation): ").strip()
        return adresse if adresse else None


# ====================================================================== #
#  MockInstrument – synthetischer Langmuirsonden-Simulator
# ====================================================================== #

class MockInstrument:
    """
    Ersatz für eine pyvisa.Resource, der eine realistische
    Langmuirsonden-I(V)-Kennlinie synthetisiert.

    Der Mock fängt :SOUR:VOLT:STAR / :STOP / :POIN Write-Befehle ab,
    um den angeforderten Sweep-Bereich zu lernen, und erzeugt synthetische
    Daten (mit Gauß'schem Rauschen) wenn :FETC:ARR? abgefragt wird.

    Die Plasmaparameter können über Konstruktor-Argumente für Tests
    angepasst werden.

    Parameter
    ---------
    v_schwebe      : float   Schwebepotential (V)
    v_plasma       : float   Plasmapotential (V)
    T_e            : float   Elektronentemperatur (eV)
    i_ion_sat      : float   Ionensättigungsstrom (A, negativ)
    i_e_sat        : float   Elektronensättigungsstrom (A, positiv)
    rauschanteil   : float   Rausch-Amplitude als Bruchteil von |i_ion_sat|
    seed           : int|None  Zufallsseed für Reproduzierbarkeit
    """

    def __init__(
        self,
        v_schwebe:    float = _SIM_V_SCHWEBE,
        v_plasma:     float = _SIM_V_PLASMA,
        T_e:          float = _SIM_T_E,
        i_ion_sat:    float = _SIM_I_ION_SAT,
        i_e_sat:      float = _SIM_I_E_SAT,
        rauschanteil: float = _SIM_RAUSCHANTEIL,
        seed:         Optional[int] = 42,
    ) -> None:
        self.timeout = 10_000  # ms – wird von pyvisa-ähnlichem Code gesetzt

        # Physikalische Parameter der Simulation speichern
        self._v_schwebe   = v_schwebe
        self._v_plasma    = v_plasma
        self._T_e         = T_e
        self._i_ion_sat   = i_ion_sat
        self._i_e_sat     = i_e_sat
        self._rauschanteil = rauschanteil
        self._rng = np.random.default_rng(seed)

        # Sweep-Parameter – werden durch write()-Aufrufe befüllt
        self._sweep: dict = {
            "v_start":  -50.0,
            "v_stop":    50.0,
            "n_punkte": 1000,
        }

    # ------------------------------------------------------------------ #
    #  VISA-kompatible Schnittstelle
    # ------------------------------------------------------------------ #

    def write(self, befehl: str) -> None:
        """SCPI Write-Befehle abfangen und Sweep-Parameter zwischenspeichern."""
        cmd      = befehl.strip()
        cmd_oben = cmd.upper()

        # Sweep-Start, -Stop und Punktzahl aus den Befehlen extrahieren
        if ":SOUR:VOLT:STAR" in cmd_oben:
            self._sweep["v_start"] = float(cmd.split()[-1])
        elif ":SOUR:VOLT:STOP" in cmd_oben:
            self._sweep["v_stop"] = float(cmd.split()[-1])
        elif ":SOUR:VOLT:POIN" in cmd_oben:
            self._sweep["n_punkte"] = int(float(cmd.split()[-1]))
        # Alle anderen Befehle werden stillschweigend akzeptiert (Konfiguration, etc.)

    def query(self, befehl: str) -> str:
        """Passende SCPI-Abfrageantworten zurückgeben."""
        cmd = befehl.strip().upper()

        if "*IDN?" in cmd:
            return "Keysight Technologies,B2910BL,MY00000000,1.0.2024.0101 [SIMULATION]"
        if "*OPC?" in cmd:
            return "1"  # Operation Complete – Sweep sofort "fertig"
        if ":FETC" in cmd:
            # Sowohl :FETC? (Skalar) als auch :FETC:ARR? (Array) behandeln
            return self._fetch_antwort_erzeugen()
        if ":SYST:ERR?" in cmd:
            return '+0,"No Error"'
        return "0"  # Standard-Fallback für nicht behandelte Abfragen

    def close(self) -> None:
        """Keine echte Verbindung vorhanden – nichts zu tun."""
        pass

    # ------------------------------------------------------------------ #
    #  Interne Hilfsmethoden – synthetische I(V)-Kurve
    # ------------------------------------------------------------------ #

    def _fetch_antwort_erzeugen(self) -> str:
        """
        Realistische Langmuirsonden-I(V)-Kurve synthetisieren und als
        kommagetrennte Zeichenkette im :FORM:ELEM:SENS VOLT,CURR-Format zurückgeben:
            V1, I1, V2, I2, ...
        """
        v_start  = self._sweep["v_start"]
        v_stop   = self._sweep["v_stop"]
        n        = self._sweep["n_punkte"]

        V, I = self._iv_kurve_berechnen(np.linspace(v_start, v_stop, n))

        # V-I-Paare verschachteln: [V1, I1, V2, I2, ...]
        paare = np.empty(2 * n)
        paare[0::2] = V
        paare[1::2] = I
        return ",".join(f"{x:.8e}" for x in paare)

    def _iv_kurve_berechnen(self, V: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Synthetische I(V)-Kennlinie berechnen.

        Physikalisches Modell:
          I_ion(V)  = I_ion_sat · (1 − α·(V − V_schwebe))   [Scheidenexpansions-Steigung]
          I_e(V)    = I_e_sat  · exp((V − V_p) / T_e)       für V < V_p
          I_e(V)    = I_e_sat  · (1 + β·(V − V_p))          für V ≥ V_p (Elektronensättigung)
          I(V)      = I_ion(V) + I_e(V) + ε                 [ε = Gauß'sches Rauschen]
        """
        # Ionenkomponente mit leichter Scheidenexpansions-Steigung
        I_ion = self._i_ion_sat * (1.0 - 0.004 * (V - self._v_schwebe))

        # Elektronenkomponente: Maxwellsche Verteilung unterhalb V_p, linear darüber
        I_e = np.where(
            V < self._v_plasma,
            self._i_e_sat * np.exp(
                np.clip((V - self._v_plasma) / self._T_e, -80, 0)  # Clipping verhindert Overflow
            ),
            self._i_e_sat * (1.0 + 0.012 * (V - self._v_plasma)),
        )

        # Gauß'sches Rauschen hinzufügen
        rausch_std = self._rauschanteil * abs(self._i_ion_sat)
        rauschen   = self._rng.normal(0.0, rausch_std, len(V))

        return V, I_ion + I_e + rauschen
