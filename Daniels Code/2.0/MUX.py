import pyvisa
import time

class Multiplexer:
    def __init__(self, ressource_address):
        self.address = ressource_address
        self.instrument = None

        try:
            rm = pyvisa.ResourceManager('@Py')
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
