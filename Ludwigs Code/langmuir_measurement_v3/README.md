# Langmuir Probe Framework for Keysight B2910BL

A modular Python framework for controlling a **Keysight B2910BL** Source
Measurement Unit and performing a full single Langmuir probe I-V analysis.

---

## Features

| Layer | Module | What it does |
|---|---|---|
| Hardware | `hardware/b2910bl_driver.py` | Full SCPI driver — every command documented |
| Connection | `hardware/connection_manager.py` | Auto-detect → manual CLI → simulation fallback |
| Physics | `physics/langmuir_analysis.py` | V_fl, V_p, T_e, I_ion_sat, I_e_sat |
| Visualisation | `visualization/plotter.py` | 3-panel Matplotlib figure with results textbox |
| Export | `utils/data_export.py` | Timestamped CSV for raw data and results |

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> **NI-VISA** (free download from ni.com) or the pure-Python **pyvisa-py**
> backend is needed for real hardware.  The simulation mode works with
> pyvisa installed but no instrument connected.

### 2. Simulation mode (no hardware)

```bash
cd langmuir_framework
python main.py --simulate
```

A synthetic Langmuir I-V curve is generated, analysed, and plotted.

### 3. Real instrument — auto-detect

Connect the B2910BL via USB, then:

```bash
python main.py
```

The script scans all VISA resources and selects the first B2910BL it finds.

### 4. Real instrument — explicit address

```bash
python main.py --address USB0::0x0957::0x8B18::MY12345678::INSTR
```

### 5. All options

```
python main.py --help
```

---

## Analysis Pipeline

```
Raw I(V)
  │
  ▼ Savitzky-Golay filter
I_smooth(V)
  │
  ├─► zero crossing ──────────────► V_fl  (floating potential)
  │
  ├─► d/dV ──► max(dI/dV) ────────► V_p   (plasma potential)
  │
  ├─► linear fit [V_min, V_fl] ───► I_ion_sat  +  poly_ion(V)
  │
  ├─► I_e = I_smooth − poly_ion(V)
  │    └─► slope of ln(I_e) in [V_fl, V_p] → T_e = 1/slope  [eV]
  │
  └─► linear fit [V_p, V_max] ────► I_e_sat  (sheath expansion corrected)
```

---

## Key Hardware Notes (B2910BL)

| Property | Value | Implementation |
|---|---|---|
| Channels | 1 (DC only — **no pulse mode** on BL variant) | `b2910bl_driver.py` |
| Voltage range | ±210 V | Validated before every sweep |
| Current range | ±1.5 A DC | Compliance default 100 mA |
| Current resolution | 10 fA | Used as noise floor in T_e fit |
| Min. trigger interval | 50 µs | Enforced in `_validate_sweep_params` |
| Trace buffer | 100 000 pts | Upper limit on `n_points` |
| High Cap. Mode | `:SENS:CURR:HCAP ON` | `--high-cap` flag |
| 4-wire sensing | `:SYST:RSEN ON` | `--remote-sense` flag |

---

## Output Files

All files are written to `measurements/` (configurable via `--output-dir`):

```
measurements/
├── langmuir_raw_20240514_093107.csv      # Raw (V, I) sweep data
└── langmuir_results_20240514_093108.csv  # Extracted plasma parameters
```

---

## Project Structure

```
langmuir_framework/
├── main.py                      ← entry point / CLI
├── requirements.txt
├── .gitignore
├── hardware/
│   ├── b2910bl_driver.py        ← SCPI driver (hardware only)
│   └── connection_manager.py   ← VISA connect + MockInstrument
├── physics/
│   └── langmuir_analysis.py    ← physics engine (pure numpy/scipy)
├── visualization/
│   └── plotter.py              ← Matplotlib 3-panel figure
└── utils/
    └── data_export.py          ← timestamped CSV export
```

---

## Example Output

```
=======================================================
  Langmuir Probe Framework — Keysight B2910BL
=======================================================
[ConnectionManager] Simulation mode — no hardware used.
[Instrument] Keysight Technologies,B2910BL,MY00000000,1.0 [SIMULATED]
[Sweep] -50.0 V → +50.0 V, 1000 points, NPLC=1.0, compliance=100 mA
[Sweep] Complete — 1000 points acquired.
[DataExporter] Raw data saved: measurements/langmuir_raw_20240514_093107.csv

[Analysis] Running physics pipeline …
========================================
  Langmuir Probe Analysis Results
========================================
  Floating potential  V_fl  =  +2.006 V
  Plasma potential    V_p   = +10.024 V
  Electron temperature T_e  =   3.012 eV
  Ion sat. current   I_ion  =  -4.987 mA
  Electron sat.      I_esat =  50.201 mA
========================================
[DataExporter] Results saved:  measurements/langmuir_results_20240514_093108.csv
[Plot] Opening interactive window …
[Done] All steps completed successfully.
```
