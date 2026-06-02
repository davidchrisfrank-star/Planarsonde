from MUX import Multiplexer
from SMU import SMU
import matplotlib
from datetime import datetime
import csv
import time

if __name__ == "__main__":
    MUX_ADDRESS = input("VISA-Addresse des MUX: ").strip()
    mux = Multiplexer(MUX_ADDRESS)

    SMU_ADDRESS = input("VISA-Addresse des MUX: ").strip()
    smu = SMU(SMU_ADDRESS)

    print("Schritt 1: ABus an Bank 1/2 anschließen.")
    time.sleep(0.5)
    ABus = input("Welchen Kanal willst du schließen? (Bspw. 1911): ").strip()
    mux.close_channel(ABus)
    time.sleep(0.5)

    print("Schritt 2: Kanal der Sonde schließen")
    channel = input("Welchen Kanal willst du messen? (Bspw (1001): ")
    mux.close_channel(channel)

    smu.config_sweep()
    smu.start_sweep()

    close = input("Willst du den Kanal öffnen? (j/n)")
    if close == "j":
        mux.open_channel(channel)


    time.sleep(5)

    Multiplexer.send("ROUT:OPEN:ALL")

    print("Programm beendet!")

