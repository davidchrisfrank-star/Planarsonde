# Langmuir Probe Automation & Analysis Framework

This production-grade Python framework controls a Keysight B2910BL SMU via SCPI commands to perform DC linear sweeps for a planar Langmuir probe. It includes an automated hardware discovery mechanism with a simulation fallback and an advanced physics engine for plasma parameter extraction.

## Features
- **Keysight B2910BL Driver:** Optimized for DC linear sweep, 10 fA resolution handling, High Capacitance mode, and 4-Wire remote sensing.
- **Physics Engine:** Advanced curve-fitting including Savitzky-Golay pre-filtering, floating/plasma potential extraction, and ion-subtracted electron temperature assessment.
- **Robustness:** Seamless simulation fallback generating synthetic noisy Langmuir curves if no physical SMU is present.

## Installation & Usage
1. Install requirements: `pip install -r requirements.txt`
2. Run the main pipeline: `python main.py`