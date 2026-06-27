"""
CSV-Lademodul für Langmuir-Rohdaten
=====================================
Unterstützt zwei Messformate:

Format A – SMU-only (Test_LPM_Control_Test.py, Modus 1):
  Spalten: Zeitstempel_PC, DMM_Zeit_Rel_S, Spannung_Soll_V, Spannung_Ist_V,
           Strom_Ist_A, Strom_SMU_A, Modus
  Auswertung: Spannung = Spannung_Ist_V, Strom = Strom_SMU_A

Format B – SMU + Keithley-DMM (Test_LPM_Control_Test.py, Modus 2):
  Gleiche Spalten, aber:
  Auswertung: Spannung = Spannung_Ist_V, Strom = Strom_Ist_A (= Keithley-Messung)

Format C – Altes Rohdaten-Format (langmuir_v4.x):
  Spalten: spannung_V, strom_A  (+ optionale Kommentarzeilen mit #)

Rückgabe immer: (V_array, I_array, meta_dict)
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional

import numpy as np


class FormatFehler(ValueError):
    """Wird ausgelöst wenn das CSV-Format nicht erkannt werden kann."""


def _zeilen_lesen(pfad: Path) -> list[list[str]]:
    """CSV lesen, Kommentarzeilen und Leerzeilen überspringen."""
    zeilen = []
    with open(pfad, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for zeile in reader:
            if not zeile:
                continue
            if zeile[0].strip().startswith("#"):
                continue
            zeilen.append([z.strip() for z in zeile])
    return zeilen


def _als_float(wert: str) -> float:
    """Robuste Float-Konvertierung; gibt NaN zurück statt Exception."""
    try:
        return float(wert)
    except (ValueError, TypeError):
        return float("nan")


def csv_laden(
    pfad: str | Path,
    strom_quelle: str = "auto",
) -> tuple[np.ndarray, np.ndarray, dict]:
    """
    CSV-Rohdatei einlesen und (V, I, meta) zurückgeben.

    Parameter
    ----------
    pfad : str | Path
        Pfad zur CSV-Datei.
    strom_quelle : str
        "smu"      → Strom_SMU_A  (SMU-Gesamtstrom, Modus 1)
        "keithley" → Strom_Ist_A  (Keithley-Plattenanteil, Modus 2)
        "auto"     → wird aus dem Dateiformat erschlossen

    Rückgabe
    --------
    V : np.ndarray   Spannungsarray (V), nach Größe sortiert
    I : np.ndarray   Stromarray (A), korrespondierend zu V
    meta : dict      Metadaten: format, n_punkte, modus, strom_quelle, datei
    """
    pfad = Path(pfad)
    if not pfad.exists():
        raise FileNotFoundError(f"Datei nicht gefunden: {pfad}")

    zeilen = _zeilen_lesen(pfad)
    if len(zeilen) < 2:
        raise FormatFehler("Datei enthält zu wenige Datenzeilen.")

    kopfzeile = [s.lower() for s in zeilen[0]]
    daten     = zeilen[1:]

    # ── Format A/B: neues Format mit 7 Spalten ──────────────────────────────
    if "spannung_ist_v" in kopfzeile:
        try:
            idx_v      = kopfzeile.index("spannung_ist_v")
            idx_i_dmm  = kopfzeile.index("strom_ist_a")
            idx_i_smu  = kopfzeile.index("strom_smu_a")
            idx_modus  = kopfzeile.index("modus") if "modus" in kopfzeile else None
        except ValueError as e:
            raise FormatFehler(f"Erwartete Spalte fehlt: {e}") from e

        # Modus aus erster Datenzeile auslesen
        modus_str = ""
        if idx_modus is not None and daten:
            modus_str = daten[0][idx_modus] if len(daten[0]) > idx_modus else ""

        # Strom-Quelle bestimmen
        if strom_quelle == "auto":
            guardring_modus = "guardring" in modus_str.lower()
            strom_quelle_final = "keithley" if guardring_modus else "smu"
        else:
            strom_quelle_final = strom_quelle

        idx_i = idx_i_dmm if strom_quelle_final == "keithley" else idx_i_smu

        V_liste, I_liste = [], []
        gesamt_eingelesen = 0
        for zeile in daten:
            if len(zeile) <= max(idx_v, idx_i):
                continue
            gesamt_eingelesen += 1
            v = _als_float(zeile[idx_v])
            i = _als_float(zeile[idx_i])
            if not (np.isnan(v) or np.isnan(i)):
                V_liste.append(v)
                I_liste.append(i)

        V = np.array(V_liste)
        I = np.array(I_liste)
        sortierung = np.argsort(V)
        V, I = V[sortierung], I[sortierung]

        meta = {
            "format":           "neu",
            "n_punkte":         len(V),
            "punkte_verworfen": gesamt_eingelesen - len(V),
            "modus":            modus_str,
            "strom_quelle":     strom_quelle_final,
            "datei":            pfad.name,
        }
        return V, I, meta

    # ── Format C: altes Format spannung_V / strom_A ─────────────────────────
    if "spannung_v" in kopfzeile or "voltage" in kopfzeile or "spannung" in kopfzeile:
        # Spaltenindex für Spannung und Strom suchen
        idx_v, idx_i = None, None
        for i, name in enumerate(kopfzeile):
            if re.search(r"spann|volt|bias", name):
                idx_v = i
            if re.search(r"strom_a$|current_a$|strom$|current$", name):
                idx_i = i
        if idx_v is None:
            idx_v = 0
        if idx_i is None:
            idx_i = 1

        V_liste, I_liste = [], []
        gesamt_eingelesen = 0
        for zeile in daten:
            if len(zeile) <= max(idx_v, idx_i):
                continue
            gesamt_eingelesen += 1
            v = _als_float(zeile[idx_v])
            i = _als_float(zeile[idx_i])
            if not (np.isnan(v) or np.isnan(i)):
                V_liste.append(v)
                I_liste.append(i)

        V = np.array(V_liste)
        I = np.array(I_liste)
        sortierung = np.argsort(V)
        V, I = V[sortierung], I[sortierung]

        meta = {
            "format":           "alt",
            "n_punkte":         len(V),
            "punkte_verworfen": gesamt_eingelesen - len(V),
            "modus":            "SMU-only",
            "strom_quelle":     "smu",
            "datei":            pfad.name,
        }
        return V, I, meta

    # ── Fallback: erste zwei numerische Spalten versuchen ───────────────────
    V_liste, I_liste = [], []
    gesamt_eingelesen = 0
    for zeile in daten:
        if len(zeile) >= 2:
            gesamt_eingelesen += 1
            v = _als_float(zeile[0])
            i = _als_float(zeile[1])
            if not (np.isnan(v) or np.isnan(i)):
                V_liste.append(v)
                I_liste.append(i)

    if len(V_liste) < 5:
        raise FormatFehler(
            f"Format nicht erkannt. Kopfzeile war: {zeilen[0]}"
        )

    V = np.array(V_liste)
    I = np.array(I_liste)
    sortierung = np.argsort(V)
    V, I = V[sortierung], I[sortierung]

    meta = {
        "format":           "unbekannt",
        "n_punkte":         len(V),
        "punkte_verworfen": gesamt_eingelesen - len(V),
        "modus":            "?",
        "strom_quelle":     "?",
        "datei":            pfad.name,
    }
    return V, I, meta
