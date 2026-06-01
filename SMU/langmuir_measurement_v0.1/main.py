import sys
from smu_driver.py import B2910BLDriver
from physics_engine.py import LangmuirAnalyzer

def main():
    print("="*60)
    print("  LANGMUIR PROBE AUTOMATION & ANALYSIS ENGINE FOR B2910BL")
    print("="*60)

    # Parametrierung der Verbindung via Hybrid-Abfrage
    manual_address = input("[?] Spezifische VISA-Adresse eingeben (Leerlassen für Auto-Erkennung): ").strip()
    if not manual_address:
        manual_address = None

    # Initialisierung des SMU-Treibers mit optimierten Parametern
    try:
        smu = B2910BLDriver(
            resource_string=manual_address,
            remote_sensing=True,  # 4-Wire aktiv
            high_cap=True,         # High Capacitance an gegen Schwingungen langer Kabel
            compliance_current=0.1 # 100 mA Grenze zum Schutz der Sonde
        )
    except Exception as e:
        print(f"[-] Kritischer Treiberfehler beim Systemstart: {e}")
        sys.exit(1)

    # Konfiguration des Sweeps (DC-Linear-Sweep angepasst an Sondencharakteristiken)
    start_voltage = -60.0
    stop_voltage = 25.0
    num_points = 2000
    trigger_interval_us = 100 # 100 µs (Hardwarelimit des BL liegt bei 50 µs)

    try:
        # Datenerfassung via SCPI oder interner Synthese-Engine
        voltages, currents = smu.execute_linear_sweep(
            start_v=start_voltage,
            stop_v=stop_voltage,
            points=num_points,
            trigger_interval_us=trigger_interval_us
        )
    finally:
        smu.close()

    # Übergabe der Datenströme an die Physik-Engine
    print("[*] Starte mathematische Reduktion und Fit-Zyklen...")
    analyzer = LangmuirAnalyzer(voltages, currents)
    results = analyzer.process_data()

    print("\n" + "-"*30 + " EXTRAHIERTE PLASMA-PARAMETER " + "-"*30)
    for key, value in results.items():
        if value is not None:
            print(f"  {key:<20} : {value:.4e}")
        else:
            print(f"  {key:<20} : Fehler beim Berechnen")
    print("-" * 88 + "\n")

    # Datenexport und Plotgenerierung
    analyzer.export_to_csv()
    analyzer.generate_plots(save_path="outputs/langmuir_analysis_plot.png")

if __name__ == "__main__":
    main()