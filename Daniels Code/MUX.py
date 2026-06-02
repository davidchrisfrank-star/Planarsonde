import pyvisa
import time

class Multiplexer:
    def __init__(self, ressource_address):
        self.address = ressource_address
        self.instrument = None

        try:
            rm = pyvisa.ResourceManager()
            self.instrument = rm.open_resource(self.address)
            self.instrument.timeout = 5000
            idn = self.instrument.query('*IDN?').strip()
            print(f"[Erfolg] Verbunden mit: {idn}")

            self.instrument.write('*CLS')

            time.sleep(0.5)

            self.instrument.write('*RST')

        except Exception as e:
            print(f"[FEHLER] {e}")
            raise

    def send(self, command):
        self.instrument.write(command)

    def close_channel(self, channel_list):
        print(f"Schließe Channel {channel_list}...")
        self.send(f"ROUT:CLOS (@{channel_list})")
        time.sleep(0.5)

    def open_channel(self, channel_list):
        print(f"Öffne Channel {channel_list}...")
        self.send(f"ROUT:OPEN (@{channel_list})")
        time.sleep(0.5)


if __name__ == "__main__":
    MUX_ADDRESS = input("VISA-Addresse des MUX: ").strip()
    mux = Multiplexer(MUX_ADDRESS)


    print("Schritt 1: ABus an Bank 1/2 anschließen.")
    time.sleep(0.5)
    ABus = input("Welchen Kanal willst du schließen? (Bspw. 1911): ").strip()
    mux.close_channel(ABus)
    time.sleep(0.5)

    print("Schritt 2: Kanal der Sonde schließen")
    channel = input("Welchen Kanal willst du messen? (Bspw (1001): ")
    mux.close_channel(channel)


    close = input("Willst du den Kanal öffnen? (j/n)")
    if close == "j":
        mux.open_channel(channel)


    time.sleep(5)

    mux.send("ROUT:OPEN:ALL")

    print("Programm beendet!")