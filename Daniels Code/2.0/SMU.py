import pyvisa
import time

class SMU:
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

    def query(self, command):
        return self.instrument.query(command).strip()

    def set_to_voltage_source(self):
        print("Stelle SMU auf Spannungsquelle")
        self.send(f":SOUR:FUNC:MODE VOLT")

    def config_sweep(self):
        print("Stelle SMU auf Sweep")
        self.send(":SOUR:VOLT:MODE:SWE")
        time.sleep(0.2)
        self.send(":SOUR:SWE:SPAC LIN")
        time.sleep(0.2)

        V_0 = input("Spannungs-Anfangswert: ")
        V_max = input("Spannungs-Endwert")

        self.send(f":SOUR:VOLT:STAR {V_0}")
        self.send(f":SOUR:VOLT:STOP {V_max}")
        self.send(":SOUR:VOLT:POIN 21")

    def start_sweep(self):
        self.send(":OUTP:ON")
        self.send(":INIT")

