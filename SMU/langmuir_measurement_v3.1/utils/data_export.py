"""
Data Export — CSV with automatic ISO-8601 timestamps
=====================================================
Two export functions:

  save_raw_data(V, I, directory)
    → langmuir_raw_YYYYMMDD_HHMMSS.csv
    Columns: voltage_V, current_A, current_mA

  save_results(results, sweep_params, directory)
    → langmuir_results_YYYYMMDD_HHMMSS.csv
    Columns: parameter, value, unit

Both functions create *directory* if it does not exist and return the
path of the file that was written.
"""

from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from physics.langmuir_analysis import LangmuirResults


def _timestamp() -> str:
    """Return an ISO-8601–style timestamp string safe for filenames."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_raw_data(
    V: np.ndarray,
    I: np.ndarray,
    directory: str | Path = "measurements",
    prefix: str = "langmuir_raw",
) -> Path:
    """
    Save raw (V, I) sweep data to a CSV file.

    Parameters
    ----------
    V : np.ndarray     Voltage array (V).
    I : np.ndarray     Current array (A).
    directory : str    Output directory; created automatically if absent.
    prefix : str       Filename prefix (before the timestamp).

    Returns
    -------
    Path   Full path to the written file.

    File format example::

        # Langmuir probe raw measurement — 2024-05-14 09:31:07
        # Points: 1000
        voltage_V,current_A,current_mA
        -50.00000,  -5.12345e-03,  -5.123
        ...
    """
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    filepath = out_dir / f"{prefix}_{_timestamp()}.csv"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(filepath, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        # Header comments (lines starting with # are skipped by np.loadtxt)
        fh.write(f"# Langmuir probe raw measurement — {now_str}\n")
        fh.write(f"# Points: {len(V)}\n")
        # Column headers
        writer.writerow(["voltage_V", "current_A", "current_mA"])
        for v, i in zip(V, I):
            writer.writerow([
                f"{v:.6g}",
                f"{i:.8e}",
                f"{i*1e3:.6g}",
            ])

    print(f"[DataExporter] Raw data saved: {filepath}")
    return filepath


def save_results(
    results: LangmuirResults,
    directory: str | Path = "measurements",
    prefix: str = "langmuir_results",
    sweep_params: Optional[dict] = None,
) -> Path:
    """
    Save extracted plasma parameters to a CSV file.

    Parameters
    ----------
    results : LangmuirResults   Analysis results from LangmuirAnalyzer.analyze().
    directory : str             Output directory; created if absent.
    prefix : str                Filename prefix.
    sweep_params : dict, optional
        Sweep configuration dict to include in the file header
        (e.g. {'v_start': -50, 'v_stop': 50, 'n_points': 1000}).

    Returns
    -------
    Path   Full path to the written file.

    File format example::

        # Langmuir probe analysis results — 2024-05-14 09:31:08
        parameter,value,unit,description
        V_fl,2.143,V,Floating potential
        V_p,10.021,V,Plasma potential
        T_e,3.042,eV,Electron temperature
        I_ion_sat,-4.987e-03,A,Ion saturation current
        I_e_sat,4.823e-02,A,Electron saturation current
    """
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    filepath = out_dir / f"{prefix}_{_timestamp()}.csv"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Ordered rows: (csv_name, value, unit, description)
    rows = [
        ("V_fl",      results.V_fl,          "V",   "Floating potential"),
        ("V_p",       results.V_p,           "V",   "Plasma potential"),
        ("T_e",       results.T_e,           "eV",  "Electron temperature"),
        ("I_ion_sat", results.I_ion_sat,     "A",   "Ion saturation current"),
        ("I_e_sat",   results.I_e_sat,       "A",   "Electron saturation current"),
        # Derived helper quantities
        ("I_ion_sat_mA", results.I_ion_sat * 1e3, "mA", "Ion saturation current"),
        ("I_e_sat_mA",   results.I_e_sat   * 1e3, "mA", "Electron saturation current"),
        # Ion fit polynomial
        ("ion_fit_slope",    results.poly_ion[0], "A/V",  "Ion saturation fit slope"),
        ("ion_fit_intercept",results.poly_ion[1], "A",    "Ion saturation fit intercept"),
        # Electron saturation fit
        ("esat_fit_slope",
         results.poly_esat[0] if results.poly_esat is not None else float("nan"),
         "A/V", "Electron saturation fit slope"),
        ("esat_fit_intercept",
         results.poly_esat[1] if results.poly_esat is not None else float("nan"),
         "A",   "Electron saturation fit intercept"),
        # T_e fit
        ("Te_fit_slope",     results.poly_te[0], "eV^-1", "ln(Ie) fit slope = 1/T_e"),
        ("Te_fit_intercept", results.poly_te[1], "",       "ln(Ie) fit intercept"),
    ]

    with open(filepath, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        fh.write(f"# Langmuir probe analysis results — {now_str}\n")
        if sweep_params:
            fh.write(f"# Sweep config: {sweep_params}\n")
        writer.writerow(["parameter", "value", "unit", "description"])
        for name, val, unit, desc in rows:
            writer.writerow([name, f"{val:.8g}", unit, desc])

    print(f"[DataExporter] Results saved:  {filepath}")
    return filepath
