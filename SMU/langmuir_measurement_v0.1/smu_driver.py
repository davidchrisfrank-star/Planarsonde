import time
import numpy as np
import pyvisa

class B2910BLDriver:
    """
    Produktionsreifer VISA-Treiber für die Keysight B2910BL SMU.
    Speziell optimiert für DC-Linear-Sweeps ohne Puls-Modus.
    """
    def __init__(self, resource_string=None, remote_sensing=True, high_cap=False, compliance_current=0.1):
        self.rm = pyvisa.ResourceManager()
        self.device = None
        self.is_simulated = False
        self.remote_sensing = remote_sensing
        self.high_cap = high_cap
        self.compliance_current = compliance_current
        
        self._establish_connection(resource_string)

    def _establish_connection(self, resource_string):
        """Implementiert die Hybrid-Verbindungslogik mit Auto-Erkennung und Fallback."""
        if resource_string:
            try:
                self.device = self.rm.open_resource(resource_string)
                self._configure_basic_settings()
                return
            except Exception as e:
                print(f"[-] Manueller Verbindungsaufbau zu {resource_string} fehlgeschlagen: {e}")
        
        print("[*] Starte Auto-Erkennung für Keysight B2910BL...")
        available_resources = self.rm.list_resources()
        keysight_resources = [res for res in available_resources if "0x0957" in res or "USB" in res]

        for res in keysight_resources:
            try:
                temp_dev = self.rm.open_resource(res)
                idn = temp_dev.query("*IDN?")
                if "B2910BL" in idn:
                    print(f"[+] Gerät automatisch erkannt: {idn.strip()} an {res}")
                    self.device = temp_dev
                    self._configure_basic_settings()
                    return
                temp_dev.close()
            except Exception:
                continue

        print("[-] Kein physisches Gerät gefunden. Wechsle in den Simulations-Modus.")
        self.is_simulated = True

    def _configure_basic_settings(self):
        """Initialisiert die SMU gemäß den hardwarespezifischen Grenzwerten."""
        self.device.timeout = 10000  # 10s Timeout für lange Sweeps
        self.device.write("*RST")    # Instrumenten-Reset
        
        # Sicherheitslimit setzen (Compliance)
        self.device.write(f":SENS:CURR:PROT {self.compliance_current}")  # Setzt Strom-Compliance limit
        
        # 4-Wire / 2-Wire Umschaltung
        if self.remote_sensing:
            self.device.write(":SENS:REM ON")   # Aktiviert Remote Sense (4-Wire) für präzise Messung am Plasma
        else:
            self.device.write(":SENS:REM OFF")  # 2-Wire Modus für koaxialen oder Multiplexer-Betrieb
            
        # High Capacitance Mode zur Schwingungsunterdrückung bei langen Zuleitungen
        if self.high_cap:
            self.device.write(":SENS:CURR:HCAP ON")   # Aktiviert Hochkapazitätsmodus gegen Oszillationen
        else:
            self.device.write(":SENS:CURR:HCAP OFF")
            
        # Aktivierung automatischer Strommessbereich für maximale Präzision (bis 10 fA)
        self.device.write(":SENS:CURR:RANGE:AUTO ON")
        self.device.write(":SENS:FUNC:ON \"CURR\"")   # Aktiviert Strommessung dediziert

    def execute_linear_sweep(self, start_v, stop_v, points, trigger_interval_us=50):
        """
        Führt einen DC-Linear-Sweep aus. Nutzt den internen Trace-Buffer.
        Puls-Modi sind hardwareseitig deaktiviert für das Modell BL.
        """
        if self.is_simulated:
            return self._generate_synthetic_data(start_v, stop_v, points)

        if points > 100000:
            raise ValueError("Modell-Limit überschritten: Maximal 100.000 Datenpunkte im Trace-Buffer.")
        
        # Minimales Trigger-Intervall absichern
        interval_sec = max(trigger_interval_us, 50) * 1e-6

        # SCPI Sweep-Konfiguration
        self.device.write(":SOUR:FUNC:MODE VOLT")              # Spannungsquellen-Modus aktivieren
        self.device.write(":SOUR:VOLT:MODE SWE")               # Sweep-Modus für Spannung aktivieren
        self.device.write(f":SOUR:VOLT:STAR {start_v}")        # Startspannung definieren
        self.device.write(f":SOUR:VOLT:STOP {stop_v}")        # Endspannung definieren
        self.device.write(f":SOUR:SWE:POIN {points}")          # Anzahl der Sweep-Schritte festlegen
        self.device.write(":SOUR:SWE:SPAC LIN")                # Linearer Sweep-Abstand

        # Trigger & Buffer Setup
        self.device.write(":TRAC:CLEAR")                       # Buffer leeren
        self.device.write(f":TRAC:POIN {points}")              # Puffergröße anpassen
        self.device.write(":TRAC:FEED SENS")                   # Sensor-Datenquelle in den Buffer speisen
        self.device.write(":TRAC:FEED:STAT CONT")              # Kontinuierliches Füllen aktivieren

        self.device.write(":TRIG:SOUR TIM")                    # Timer-gesteuerter Trigger
        self.device.write(f":TRIG:TIM {interval_sec}")         # Trigger-Intervall setzen (min 50µs)
        self.device.write(f":TRIG:COUN {points}")              # Anzahl der benötigten Trigger-Impulse

        # Start Messung
        print(f"[*] Starte Sweep von {start_v} V bis {stop_v} V ({points} Punkte)...")
        self.device.write(":INIT")
        
        # Blockieren bis Messung beendet ist via Operation Complete
        self.device.query("*OPC?")
        
        # Daten abfragen (Formatierung auf Spannung und Strom begrenzen)
        self.device.write(":FORM:ELEM:SENS VOLT,CURR")
        raw_data = self.device.query_ascii_values(":TRAC:DATA?")
        
        # Umsortieren der verschachtelten Rückgabewerte [V1, I1, V2, I2, ...]
        data_array = np.array(raw_data).reshape(-1, 2)
        voltages = data_array[:, 0]
        currents = data_array[:, 1]
        
        return voltages, currents

    def _generate_synthetic_data(self, start_v, stop_v, points):
        """Generiert eine physikalisch plausible Langmuir-Kennlinie inkl. Messrauschen."""
        voltages = np.linspace(start_v, stop_v, points)
        
        # Plasma-Parameter für Synthese
        v_fl = -15.0
        v_p = 5.0
        t_e_sim = 3.5  # eV
        i_ion_sat = -0.002  # -2 mA
        i_e_sat = 0.05      # 50 mA
        
        currents = np.zeros_like(voltages)
        for i, v in enumerate(voltages):
            if v < v_p:
                # Ionenstrom + exponentieller Elektronenanstieg
                i_ion = i_ion_sat * (1 - 0.005 * (v - v_fl))  # Leichte Schichtaufweitung links
                i_elec = 0.008 * np.exp((v - v_fl) / t_e_sim)
                currents[i] = i_ion + i_elec
            else:
                # Elektronensättigungsbereich mit Schichtaufweitung (Sheath Expansion)
                i_ion = i_ion_sat * (1 - 0.005 * (v - v_fl))
                i_elec_sat = i_e_sat * (1 + 0.01 * (v - v_p))
                currents[i] = i_ion + i_elec_sat
                
        # Rauschüberlagerung (unter Berücksichtigung des 10 fA Limits, skaliert auf nA/mA Rauschen)
        noise = np.random.normal(0, 1e-4, size=points)
        return voltages, currents + noise

    def close(self):
        if self.device:
            self.device.close()
            self.rm.close()