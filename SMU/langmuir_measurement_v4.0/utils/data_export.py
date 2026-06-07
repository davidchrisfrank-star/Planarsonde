"""
CSV-Datenexport mit automatischen ISO-8601-Zeitstempeln
=======================================================
Zwei Export-Funktionen:

  rohdaten_speichern(V, I, verzeichnis)
    → langmuir_rohdaten_JJJJMMTT_HHMMSS.csv
    Spalten: spannung_V, strom_A, strom_mA

  ergebnisse_speichern(ergebnisse, sweep_parameter, verzeichnis)
    → langmuir_ergebnisse_JJJJMMTT_HHMMSS.csv
    Spalten: parameter, wert, einheit, beschreibung

Beide Funktionen erstellen das Verzeichnis automatisch falls es nicht existiert
und geben den vollständigen Pfad der geschriebenen Datei zurück.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from physics.langmuir_analysis import LangmuirResults


def _zeitstempel() -> str:
    """Gibt einen ISO-8601-ähnlichen Zeitstempel zurück, der in Dateinamen verwendet werden kann."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rohdaten_speichern(
    V: np.ndarray,
    I: np.ndarray,
    verzeichnis: str | Path = "messungen",
    praefix:     str        = "langmuir_rohdaten",
) -> Path:
    """
    Rohe (V, I)-Sweep-Daten in eine CSV-Datei schreiben.

    Parameter
    ---------
    V : np.ndarray     Spannungsarray (V).
    I : np.ndarray     Stromarray (A).
    verzeichnis : str  Ausgabeverzeichnis; wird automatisch erstellt falls nicht vorhanden.
    praefix : str      Dateinamen-Präfix (vor dem Zeitstempel).

    Rückgabe
    --------
    Path   Vollständiger Pfad der geschriebenen Datei.

    Dateiformat-Beispiel::

        # Langmuirsonde Rohmessung — 2024-05-14 09:31:07
        # Punkte: 1000
        spannung_V,strom_A,strom_mA
        -50.00000,  -5.12345e-03,  -5.123
        ...
    """
    ausgabe_pfad = Path(verzeichnis)
    ausgabe_pfad.mkdir(parents=True, exist_ok=True)  # Verzeichnis anlegen falls nötig

    dateipfad = ausgabe_pfad / f"{praefix}_{_zeitstempel()}.csv"
    jetzt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(dateipfad, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Kommentarzeilen mit Metadaten (werden von np.loadtxt mit # übersprungen)
        f.write(f"# Langmuirsonde Rohmessung — {jetzt_str}\n")
        f.write(f"# Punkte: {len(V)}\n")
        # Spaltenüberschriften
        writer.writerow(["spannung_V", "strom_A", "strom_mA"])
        # Messwerte Zeile für Zeile schreiben
        for v, i in zip(V, I):
            writer.writerow([
                f"{v:.6g}",        # Spannung in V
                f"{i:.8e}",        # Strom in A (wissenschaftliche Notation)
                f"{i * 1e3:.6g}",  # Strom in mA für einfachere Lesbarkeit
            ])

    print(f"[Export] Rohdaten gespeichert: {dateipfad}")
    return dateipfad


# Alias für Rückwärtskompatibilität (GUI ruft save_raw_data() auf)
def save_raw_data(
    V: np.ndarray,
    I: np.ndarray,
    directory: str | Path = "messungen",
    prefix:    str        = "langmuir_rohdaten",
) -> Path:
    return rohdaten_speichern(V, I, directory, prefix)


def ergebnisse_speichern(
    ergebnisse:      LangmuirResults,
    verzeichnis:     str | Path     = "messungen",
    praefix:         str            = "langmuir_ergebnisse",
    sweep_parameter: Optional[dict] = None,
) -> Path:
    """
    Extrahierte Plasmaparameter in eine CSV-Datei schreiben.

    Parameter
    ---------
    ergebnisse : LangmuirResults
        Analyseergebnisse von LangmuirAnalyzer.analyze().
    verzeichnis : str
        Ausgabeverzeichnis; wird erstellt falls nicht vorhanden.
    praefix : str
        Dateinamen-Präfix.
    sweep_parameter : dict, optional
        Sweep-Konfigurations-Dictionary für den Datei-Header,
        z. B. {'v_start': -50, 'v_stop': 50, 'n_punkte': 1000}.

    Rückgabe
    --------
    Path   Vollständiger Pfad der geschriebenen Datei.

    Dateiformat-Beispiel::

        # Langmuirsonde Analyseergebnisse — 2024-05-14 09:31:08
        parameter,wert,einheit,beschreibung
        V_fl,2.143,V,Schwebepotential
        V_p,10.021,V,Plasmapotential
        T_e,3.042,eV,Elektronentemperatur
        I_ion_sat,-4.987e-03,A,Ionensättigungsstrom
        I_e_sat,4.823e-02,A,Elektronensättigungsstrom
    """
    ausgabe_pfad = Path(verzeichnis)
    ausgabe_pfad.mkdir(parents=True, exist_ok=True)

    dateipfad = ausgabe_pfad / f"{praefix}_{_zeitstempel()}.csv"
    jetzt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Alle zu exportierenden Zeilen als (Name, Wert, Einheit, Beschreibung)
    zeilen = [
        # Hauptplasmaparameter
        ("V_fl",         ergebnisse.V_fl,          "V",     "Schwebepotential"),
        ("V_p",          ergebnisse.V_p,            "V",     "Plasmapotential"),
        ("T_e",          ergebnisse.T_e,            "eV",    "Elektronentemperatur"),
        ("I_ion_sat",    ergebnisse.I_ion_sat,       "A",     "Ionensättigungsstrom"),
        ("I_e_sat",      ergebnisse.I_e_sat,         "A",     "Elektronensättigungsstrom"),
        # Gleiche Ströme in Milliampere für einfachere Lesbarkeit
        ("I_ion_sat_mA", ergebnisse.I_ion_sat * 1e3, "mA",   "Ionensättigungsstrom"),
        ("I_e_sat_mA",   ergebnisse.I_e_sat   * 1e3, "mA",   "Elektronensättigungsstrom"),
        # Ionensättigungs-Fit-Koeffizienten
        ("ion_fit_steigung",       ergebnisse.poly_ion[0], "A/V", "Ionensättigungs-Fit Steigung"),
        ("ion_fit_achsenabschnitt", ergebnisse.poly_ion[1], "A",   "Ionensättigungs-Fit Achsenabschnitt"),
        # Elektronensättigungs-Fit-Koeffizienten (NaN wenn nicht genug Punkte)
        ("esat_fit_steigung",
         ergebnisse.poly_esat[0] if ergebnisse.poly_esat is not None else float("nan"),
         "A/V", "Elektronensättigungs-Fit Steigung"),
        ("esat_fit_achsenabschnitt",
         ergebnisse.poly_esat[1] if ergebnisse.poly_esat is not None else float("nan"),
         "A",   "Elektronensättigungs-Fit Achsenabschnitt"),
        # T_e-Fit-Koeffizienten
        ("Te_fit_steigung",        ergebnisse.poly_te[0], "eV^-1", "ln(Ie)-Fit Steigung = 1/T_e"),
        ("Te_fit_achsenabschnitt", ergebnisse.poly_te[1], "",      "ln(Ie)-Fit Achsenabschnitt"),
    ]

    with open(dateipfad, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Kopfzeilen mit Metadaten
        f.write(f"# Langmuirsonde Analyseergebnisse — {jetzt_str}\n")
        if sweep_parameter:
            f.write(f"# Sweep-Konfiguration: {sweep_parameter}\n")
        # Spaltenüberschriften
        writer.writerow(["parameter", "wert", "einheit", "beschreibung"])
        # Ergebnisse Zeile für Zeile schreiben
        for name, wert, einheit, beschreibung in zeilen:
            writer.writerow([name, f"{wert:.8g}", einheit, beschreibung])

    print(f"[Export] Ergebnisse gespeichert: {dateipfad}")
    return dateipfad


# Alias für Rückwärtskompatibilität (GUI ruft save_results() auf)
def save_results(
    results:       LangmuirResults,
    directory:     str | Path     = "messungen",
    prefix:        str            = "langmuir_ergebnisse",
    sweep_params:  Optional[dict] = None,
) -> Path:
    return ergebnisse_speichern(results, directory, prefix, sweep_params)
