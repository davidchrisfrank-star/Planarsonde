"""
Langmuir Probe Framework — Entry Point
========================================
Usage examples:

  # Simulation (no hardware required):
  python main.py --simulate

  # Auto-detect Keysight B2910BL on USB:
  python main.py

  # Explicit VISA address, 4-wire sensing, save plot:
  python main.py --address USB0::0x0957::0x8B18::MY12345::INSTR \\
                 --remote-sense --save-plot sweep.png

  # Custom sweep range and High Capacitance Mode:
  python main.py --simulate --v-start -40 --v-stop 40 --points 2000 --high-cap

Run  python main.py --help  for full option reference.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import numpy as np

# --------------------------------------------------------------------------- #
#  Module imports — adjust sys.path so sub-packages resolve correctly when
#  main.py is invoked directly (not as part of an installed package).
# --------------------------------------------------------------------------- #
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from hardware.connection_manager import ConnectionManager
from hardware.b2910bl_driver import B2910BLDriver
from physics.langmuir_analysis import LangmuirAnalyzer, AnalysisError
from visualization.plotter import LangmuirPlotter
from utils.data_export import save_raw_data, save_results


# ====================================================================== #
#  CLI argument parser
# ====================================================================== #

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="langmuir",
        description=(
            "Langmuir Probe I-V Characterisation Framework\n"
            "Controls a Keysight B2910BL SMU and extracts plasma parameters\n"
            "  V_fl  (floating potential), V_p  (plasma potential),\n"
            "  T_e   (electron temperature),  I_ion_sat,  I_e_sat.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ---- Connection ----
    grp = p.add_argument_group("Connection")
    grp.add_argument(
        "--simulate", action="store_true",
        help="Use synthetic data — no hardware required.",
    )
    grp.add_argument(
        "--address", metavar="VISA_ADDR", default=None,
        help=(
            "VISA resource string, e.g. "
            "USB0::0x0957::0x8B18::MY12345678::INSTR. "
            "If omitted, auto-detection is attempted."
        ),
    )

    # ---- Sweep parameters ----
    grp = p.add_argument_group("Sweep Parameters")
    grp.add_argument("--v-start",  type=float, default=-50.0,
                     metavar="V",  help="Start voltage (V). Default: -50")
    grp.add_argument("--v-stop",   type=float, default=50.0,
                     metavar="V",  help="Stop voltage (V).  Default: +50")
    grp.add_argument("--points",   type=int,   default=1000,
                     metavar="N",  help="Number of sweep points. Default: 1000")
    grp.add_argument("--trig-interval", type=float, default=200e-6,
                     metavar="S",
                     help="Trigger interval (s). Min 50 µs. Default: 200 µs")

    # ---- Instrument settings ----
    grp = p.add_argument_group("Instrument Settings")
    grp.add_argument("--compliance", type=float, default=0.1,
                     metavar="A",
                     help="Current compliance / safety limit (A). Default: 0.1 A")
    grp.add_argument("--nplc", type=float, default=1.0,
                     metavar="PLC",
                     help="Integration time in Power Line Cycles. Default: 1.0")
    grp.add_argument("--high-cap", action="store_true",
                     help="Enable High Capacitance Mode (:SENS:CURR:HCAP ON).")
    grp.add_argument("--remote-sense", action="store_true",
                     help="Enable 4-wire Kelvin sensing (:SYST:RSEN ON).")

    # ---- Physics analysis ----
    grp = p.add_argument_group("Physics Analysis")
    grp.add_argument("--ion-fit-start", type=float, default=-50.0,
                     metavar="V",
                     help="Start of ion saturation fit region (V). Default: -50")
    grp.add_argument("--ion-fit-stop",  type=float, default=-30.0,
                     metavar="V",
                     help="End of ion saturation fit region (V).   Default: -30")
    grp.add_argument("--savgol-window", type=int, default=51,
                     metavar="N",
                     help="Savitzky-Golay filter window length (odd). Default: 51")

    # ---- Output ----
    grp = p.add_argument_group("Output")
    grp.add_argument("--output-dir", default="measurements",
                     metavar="DIR",
                     help="Directory for CSV files. Default: measurements/")
    grp.add_argument("--no-plot", action="store_true",
                     help="Skip the Matplotlib figure (headless / CI mode).")
    grp.add_argument("--save-plot", default=None,
                     metavar="FILE",
                     help="Save figure to FILE (e.g. sweep.png) instead of showing it.")

    return p


# ====================================================================== #
#  Main
# ====================================================================== #

def main(argv=None) -> int:
    """
    Orchestrates the full measurement and analysis pipeline.

    Returns
    -------
    int   Exit code: 0 on success, 1 on error.
    """
    args = _build_parser().parse_args(argv)

    print("\n" + "=" * 55)
    print("  Langmuir Probe Framework  |  Keysight B2910BL")
    print("=" * 55)

    # ------------------------------------------------------------------ #
    #  Step 1 — Connect to instrument (or start simulation)
    # ------------------------------------------------------------------ #
    manager = ConnectionManager(simulate=args.simulate)
    try:
        instrument = manager.connect(address=args.address)
    except ConnectionError as exc:
        print(f"\n[ERROR] {exc}")
        return 1

    # ------------------------------------------------------------------ #
    #  Step 2 — Configure and run sweep
    # ------------------------------------------------------------------ #
    driver = B2910BLDriver(
        instrument=instrument,
        compliance_current=args.compliance,
        nplc=args.nplc,
        high_cap_mode=args.high_cap,
        remote_sensing=args.remote_sense,
    )

    try:
        print(f"\n[Instrument] {driver.identify()}")
        driver.reset()
        driver.configure()

        print(
            f"\n[Sweep] {args.v_start:+.1f} V to {args.v_stop:+.1f} V, "
            f"{args.points} points, "
            f"NPLC={args.nplc}, compliance={args.compliance*1e3:.0f} mA"
        )
        if args.high_cap:
            print("[Sweep] High Capacitance Mode: ON")
        if args.remote_sense:
            print("[Sweep] Remote Sensing (4-wire): ON")

        V, I = driver.run_sweep(
            v_start=args.v_start,
            v_stop=args.v_stop,
            n_points=args.points,
            trigger_interval=args.trig_interval,
        )
        print(f"[Sweep] Complete — {len(V)} points acquired.")

    except (ValueError, RuntimeError) as exc:
        print(f"\n[ERROR] Sweep failed: {exc}")
        manager.disconnect()
        return 1

    finally:
        manager.disconnect()

    # ------------------------------------------------------------------ #
    #  Step 3 — Save raw data
    # ------------------------------------------------------------------ #
    raw_path = save_raw_data(V, I, directory=args.output_dir)

    # ------------------------------------------------------------------ #
    #  Step 4 — Physics analysis
    # ------------------------------------------------------------------ #
    print("\n[Analysis] Running physics pipeline ...")
    analyzer = LangmuirAnalyzer(
        V=V,
        I=I,
        savgol_window=args.savgol_window,
        ion_fit_range=(args.ion_fit_start, args.ion_fit_stop),
    )

    try:
        results = analyzer.analyze()
    except AnalysisError as exc:
        print(f"\n[ERROR] Analysis failed: {exc}")
        print(
            "Tip: try adjusting --ion-fit-start / --ion-fit-stop "
            "or widening the sweep range."
        )
        return 1

    print(results.summary())

    # ------------------------------------------------------------------ #
    #  Step 5 — Save results
    # ------------------------------------------------------------------ #
    sweep_params = {
        "v_start": args.v_start,
        "v_stop": args.v_stop,
        "n_points": args.points,
        "compliance_A": args.compliance,
        "nplc": args.nplc,
        "high_cap": args.high_cap,
        "remote_sense": args.remote_sense,
    }
    save_results(results, directory=args.output_dir, sweep_params=sweep_params)

    # ------------------------------------------------------------------ #
    #  Step 6 — Visualise
    # ------------------------------------------------------------------ #
    if not args.no_plot:
        plotter = LangmuirPlotter(V, I, analyzer, results)

        if args.save_plot:
            plotter.save(args.save_plot)
            print(f"[Plot] Saved to: {args.save_plot}")
        else:
            print("[Plot] Opening interactive window …")
            plotter.show()

    print("\n[Done] All steps completed successfully.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
