# ==============================================================================
# IMPORTS (Teil 1 & 2)
# ==============================================================================
import time
import serial
import pyvisa
import csv
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="gpib_ctypes")
from datetime import datetime

# ==============================================================================
# 1. GERÄTE-KLASSEN (HARDWARE-ABSTRAKTION)
# ==============================================================================

class KeysightMultiplexer:
    """Steuerung des Keysight 34923A/34980A Multiplexers mit BBM-Sicherheit und automatischer GND-Erdung auf Block 2."""
    def __init__(self, resource_address, simulate=False):
        self.simulate = simulate
        self.address = resource_address
        self.instrument = None

        if self.simulate:
            print(f"[SIMULATION] Virtueller Multiplexer an {self.address} aktiviert.")
        else:
            try:
                rm = pyvisa.ResourceManager('@py') 
                self.instrument = rm.open_resource(self.address)
                self.instrument.timeout = 5000
                idn = self.instrument.query('*IDN?').strip()
                print(f"[ERFOLG] Multiplexer verbunden: {idn}")
                
                self.instrument.write('*RST')
                self.instrument.write('*CLS')
                self.instrument.query("*OPC?")
                
                self.read_errors()
            except Exception as e:
                print(f"[FEHLER] Verbindung zum Multiplexer fehlgeschlagen: {e}")
                raise

    def read_errors(self):
        """Fragt den internen Fehlerspeicher des Keysight-Mainframes ab."""
        if self.simulate:
            return
        
        errors = []
        try:
            while True:
                err = self.instrument.query("SYST:ERR?").strip()
                if '+0,"No error"' in err or '0,"No error"' in err:
                    break
                errors.append(err)
            
            if errors:
                print(f"  \033[93m[KEYSIGHT DISPLAY-FEHLER REGISTRIERT]\033[0m {errors}")
        except Exception as e:
            print(f"  [INFO] Fehler beim Auslesen des Keysight-Fehlerspeichers: {e}")

    def _close_unused_block2_to_gnd(self, active_channels_b1):
        """Schließt ungenutzte Kanäle auf Block 2 gegen COM2 (GND)."""
        reale_sonden = ["1001", "1002", "1003", "1004", "1005"]
        all_channels_b2 = [str(int(ch) + 20) for ch in reale_sonden]
        floating_channels_b2 = [str(int(ch) + 20) for ch in active_channels_b1]
        gnd_channels = [ch for ch in all_channels_b2 if ch not in floating_channels_b2]
        
        if not gnd_channels:
            return

        ch_string = ",".join(gnd_channels)

        if self.simulate:
            print(f"  [SIM MUX] GND-SCHALTUNG: Kanäle {floating_channels_b2} bleiben FLOATING.")
            print(f"  [SIM MUX] GND-SCHALTUNG: Schließe reale ungenutzte Kanäle ({ch_string}) gegen COM2 (GND).")
            return

        self.instrument.write(f"ROUT:CLOS (@{ch_string})")

    def safe_switch_to_channel(self, active_channel, abus="1911"):
        """MODUS 1: Schaltet einen einzelnen Sondenkanal."""
        if self.simulate:
            print(f"\n  [SIM MUX] BBM-SWITCH (Modus 1): Aktiviere Sonde {active_channel} über ABus {abus}")
            self._close_unused_block2_to_gnd([active_channel])
            return

        try:
            self.instrument.write("ROUT:OPEN:ALL ALL")
            self.instrument.query("*OPC?") 
            
            self.instrument.write(f"ROUT:CLOS (@{abus})")             
            self.instrument.write(f"ROUT:CLOS (@{active_channel})") 
            
            self._close_unused_block2_to_gnd([active_channel])
            
            self.instrument.query("*OPC?") 
            print(f"  [SAFETY] Modus 1 aktiv: Sonde {active_channel} misst über ABus {abus}. Restliche Sonden geerdet.")
            self.read_errors()
        except Exception as e:
            print(f"  [CRITICAL] Fehler beim BBM-Routing in Modus 1: {e}")
            self.open_all()
            raise

    def safe_switch_to_guardring(self, guard_channel, plate_channel):
        """MODUS 2: Schaltet Guard (ABus1) & Platte (ABus2) parallel via BBM."""
        if self.simulate:
            print(f"\n  [SIM MUX] BBM-SWITCH (Modus 2): Guard {guard_channel} & Platte {plate_channel}")
            self._close_unused_block2_to_gnd([guard_channel, plate_channel])
            return

        try:
            self.instrument.write("ROUT:OPEN:ALL ALL")
            self.instrument.query("*OPC?")
            
            self.instrument.write("ROUT:CLOS (@1911)")             
            self.instrument.write(f"ROUT:CLOS (@{guard_channel})")  
            
            self.instrument.write("ROUT:CLOS (@1912)")             
            self.instrument.write(f"ROUT:CLOS (@{plate_channel})")  
            
            self._close_unused_block2_to_gnd([guard_channel, plate_channel])
            
            self.instrument.query("*OPC?")
            print(f"  [SAFETY] Modus 2 aktiv: Guard ({guard_channel}) & Platte ({plate_channel}) messen. Restliche Sonden geerdet.")
            self.read_errors()
        except Exception as e:
            print(f"  [CRITICAL] Fehler beim Guardring-Routing in Modus 2: {e}")
            self.open_all()
            raise

    def open_all(self):
        """Sicherer Not-Aus: Trennt alle Kanäle."""
        if self.simulate:
            print("  [SIM MUX] Not-Aus: Öffne ALLE Kanäle und Busse.")
        else:
            self.instrument.write("ROUT:OPEN:ALL ALL")
            self.instrument.query("*OPC?")
            self.read_errors()


class KeysightSMU:
    """Steuerung der Keysight B2910BL SMU (Optimiert auf 2ms Hardware-Integration)."""
    def __init__(self, resource_address, simulate=False):
        self.simulate = simulate
        self.address = resource_address
        self.instrument = None

        if self.simulate:
            print(f"[SIMULATION] Virtuelle SMU an {self.address} aktiviert.")
        else:
            try:
                rm = pyvisa.ResourceManager('@py')
                self.instrument = rm.open_resource(self.address)
                self.instrument.timeout = 5000
                idn = self.instrument.query('*IDN?').strip()
                print(f"[ERFOLG] SMU verbunden: {idn}")
            except Exception as e:
                print(f"[FEHLER] Verbindung zur SMU fehlgeschlagen: {e}")
                raise

    def init_software_sweep(self, compliance_current=0.001):
        """Bereitet die SMU auf High-Speed-Sweeps vor (0.1 NPLC = 2ms Messzeit)."""
        if self.simulate:
            print(f"  [SIM SMU] Initialisiere Spannungsmodus (Compliance: {compliance_current}A)")
            return
        
        self.instrument.write("*RST")
        self.instrument.write(":SOUR:FUNC:MODE VOLT")
        self.instrument.write(f":SENS:CURR:PROT {compliance_current}")
        self.instrument.write(":SENS:CURR:RANG 100e-6") 
        self.instrument.write(":SENS:CURR:NPLC 0.1")
        self.instrument.write("*CLS")

    def set_voltage(self, voltage):
        self.instrument.write(f":SOUR:VOLT {voltage}")

    def measure_current(self):
        """Liest den aktuellen Ist-Strom von der SMU aus (Gesamtstrom)."""
        if self.simulate:
            return 5e-6 
        res = self.instrument.query(":MEAS:CURR?")
        try:
            return float(res.split(',')[0])
        except ValueError:
            return 0.0

    def measure_voltage(self):
        """Liest die aktuell real anliegende Ist-Spannung der SMU aus."""
        if self.simulate:
            return 5.0
        res = self.instrument.query(":MEAS:VOLT?")
        try:
            return float(res.split(',')[0])
        except ValueError:
            return 0.0

    def output_on(self):
        if not self.simulate: self.instrument.write(":OUTP ON")

    def output_off(self):
        if not self.simulate: self.instrument.write(":OUTP OFF")


class KeithleyDMM:
    """Steuerung des Keithley 2700 mit 19200 Baud und Auto-Retry-Kaltstartschutz."""
    def __init__(self, port="/dev/ttyUSB0", simulate=False):
        self.simulate = simulate
        self.port = port
        self.ser = None

        if self.simulate:
            print(f"[SIMULATION] Virtuelles Keithley DMM an {self.port} aktiviert.")
            return
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=19200, 
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5
            )
            if self.ser.is_open:
                print(f"[ERFOLG] Keithley DMM am Port {self.port} geöffnet!")
                self.ser.reset_input_buffer()
                self.ser.reset_output_buffer()
                time.sleep(0.1)
                self.ser.write(b"\r\n")
                time.sleep(0.1)
                self.ser.reset_input_buffer()
                
                self.ser.write(b"*IDN?\r\n")
                time.sleep(0.2) 
                idn = self.ser.readline().decode("utf-8", errors="ignore").strip()
                print(f"  Geräte-Info: {idn}" if idn else "  Geräte-Info: [Verbindung steht]")
        except Exception as e:
            print(f"[FEHLER] Verbindung zum Keithley fehlgeschlagen: {e}")
            raise

    def init_current_measurement(self):
        """Konfiguriert das Keithley sauber auf DC-Strom und optimiert die Befehlsreihenfolge."""
        if self.simulate:
            return
        
        self.ser.write(b"*RST\r\n")
        time.sleep(0.1)
        self.ser.write(b"*CLS\r\n")
        time.sleep(0.05)
        
        self.ser.write(b":SENS:FUNC 'CURR:DC'\r\n")
        self.ser.write(b":SENS:CURR:DC:RANG 100e-6\r\n") 
        self.ser.write(b":SENS:CURR:DC:NPLC 0.1\r\n")   
        self.ser.write(b":SENS:CURR:DC:AVER:STAT OFF\r\n") 
        
        self.ser.write(b":SYST:AZER:STAT OFF\r\n") 
        self.ser.write(b":FORM:ELEM READ\r\n") 
        self.ser.write(b":INIT:CONT OFF\r\n")       
        
        self.ser.write(b":DISP:ENAB OFF\r\n")       
        
        time.sleep(0.1)
        self.ser.write(b"*CLS\r\n")

    def read_current(self):
        """Liest den Puffer aus. Macht bei leerem Puffer automatisch einen schnellen zweiten Versuch."""
        if self.simulate:
            return 1.2e-6, 0.0
            
        for versuch in range(2):
            try:
                self.ser.reset_input_buffer()
                self.ser.write(b"READ?\r\n")
                time.sleep(0.060)
                
                raw_data = ""
                while self.ser.in_waiting > 0:
                    char = self.ser.read(1).decode("utf-8", errors="ignore")
                    if char in ("\n", "\r"):
                        if raw_data:
                            break
                    else:
                        raw_data += char
                
                if not raw_data:
                    raw_bytes = self.ser.readline()
                    raw_data = raw_bytes.decode("utf-8", errors="ignore").strip()

                if raw_data:
                    str_strom = "".join([z for z in raw_data if z in "0123456789.-+Ee"])
                    if str_strom:
                        return float(str_strom), 0.0
                        
                time.sleep(0.05)
                
            except Exception:
                pass
                
        return float('nan'), 0.0

    def close(self):
        if not self.simulate and self.ser and self.ser.is_open:
            self.ser.write(b":DISP:ENAB ON\r\n") 
            self.ser.close()
            print("  Keithley-Schnittstelle sauber geschlossen.")


# ==============================================================================
# 2. HILFSFUNKTIONEN & DATEN-HANDLING
# ==============================================================================

def sweep_abfrage():
    print("\n--- SWEEP-PARAMETER FESTLEGEN ---")
    try:
        start = float(input("Startspannung (V) [z.B. -10]: "))
        stop = float(input("Stoppspannung (V) [z.B. 10]: "))
        schrittweite = float(input("Schrittgröße (V) [z.B. 1.0 oder 0.5]: "))
        
        if schrittweite <= 0:
            schrittweite = 1.0
            
        spanne = stop - start
        punkte = int(round(abs(spanne) / schrittweite)) + 1
        if punkte < 2: punkte = 2
            
        spannungs_liste = [start + (stop - start) * i / (punkte - 1) for i in range(punkte)]
        print(f"[INFO] Generierte Messreihe mit {punkte} Punkten")
        return spannungs_liste
    except ValueError:
        return [(-5.0 + 1.0 * i) for i in range(11)]


def save_to_csv(data, filename, mode_label):
    try:
        with open(filename, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Spaltenstruktur um "Strom_SMU_A" erweitert
            writer.writerow(["Zeitstempel_PC", "DMM_Zeit_Rel_S", "Spannung_Soll_V", "Spannung_Ist_V", "Strom_Ist_A", "Strom_SMU_A", "Modus"])
            writer.writerows(data)
        print(f"[ERFOLG] Daten erfolgreich gespeichert unter: {filename}")
    except Exception as e:
        print(f"[FEHLER] Fehler beim Speichern der CSV-Datei: {e}")

# ==============================================================================
# 3. MESS-MODI (LOGIK)
# ==============================================================================

def modus_1_einfache_sonde(mux, smu, alle_kanaele):
    print("\n=== MODUS 1: EINFACHE LANGMUIRSONDE ===")
    kanal_raw = input("Welcher Multiplexer-Kanal wird verwendet? (z.B. 1002): ").strip().replace("@", "")
    if kanal_raw not in alle_kanaele: return
        
    spannungen = sweep_abfrage()
    csv_datei = f"langmuir_einfach_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    messdaten = []

    try:
        smu.init_software_sweep(compliance_current=0.005) 
        mux.safe_switch_to_channel(active_channel=kanal_raw)
        time.sleep(0.5)
        smu.output_on()
        
        print("\nStarte Messreihe (Software-Sweep)...")
        for u_soll in spannungen:
            smu.set_voltage(u_soll)
            time.sleep(0.01) 
            
            i_ist = smu.measure_current()
            u_ist = smu.measure_voltage()
            
            zeit = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            # Modus 1 nutzt nur SMU-Werte (Strom_Ist_A und Strom_SMU_A sind hier identisch)
            messdaten.append([zeit, 0.0, u_soll, u_ist, i_ist, i_ist, "Einfache Sonde"])
            print(".", end="", flush=True)
            
    finally:
        smu.output_off()
        mux.open_all()
        
    if messdaten: print("\n[INFO] Sweep beendet."); save_to_csv(messdaten, csv_datei, "Modus 1")


def modus_2_guardring_sonde(mux, smu, k_dmm, alle_kanaele):
    print("\n=== MODUS 2: LANGMUIRSONDE MIT GUARDRING ===")
    kanal_guard = input("Kanal für GUARDRING (z.B. 1001): ").strip().replace("@", "")
    kanal_platte = input("Kanal für MESSPLATTE (z.B. 1002): ").strip().replace("@", "")
    
    if (kanal_guard not in alle_kanaele) or (kanal_platte not in alle_kanaele):
        print("[FEHLER] Kanal-Konfigurationsfehler!")
        return
        
    spannungen = sweep_abfrage()
    csv_datei = f"langmuir_guardring_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    messdaten = []

    try:
        smu.init_software_sweep(compliance_current=0.020) 
        k_dmm.init_current_measurement()
        
        mux.safe_switch_to_guardring(guard_channel=kanal_guard, plate_channel=kanal_platte)
        time.sleep(0.5)
        smu.output_on()
        
        print("  [INFO] Synchronisiere DMM-Hardware-Modus...")
        smu.set_voltage(spannungen[0])
        time.sleep(0.2)  
        _, _ = k_dmm.read_current()  
        time.sleep(0.05)
        
        print("\nStarte präzisen High-Speed Guardring-Sweep...")
        for u_soll in spannungen:
            smu.set_voltage(u_soll)
            time.sleep(0.01) 
            
            # 1. Präzisen Plasmastrom der Platte vom Keithley holen
            i_platte, dmm_zeit_rel = k_dmm.read_current()
            
            # 2. Exakte Ist-Spannung von der SMU abfragen
            u_ist = smu.measure_voltage()
            
            # 3. JETZT NEU: Den Gesamtstrom (Platte + Guardring) live von der SMU loggen
            i_smu = smu.measure_current()
            
            zeit_pc = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            
            # Alle Messwerte (Keithley-Strom und SMU-Strom) parallel sichern
            messdaten.append([zeit_pc, dmm_zeit_rel, u_soll, u_ist, i_platte, i_smu, "Guardring Sonde"])
            print(".", end="", flush=True) 
            
    finally:
        smu.output_off()
        if not k_dmm.simulate and k_dmm.ser:
            k_dmm.ser.write(b":DISP:ENAB ON\r\n") 
        mux.open_all()
        
    if messdaten:
        print("\n[INFO] Sweep beendet!")
        save_to_csv(messdaten, csv_datei, "Modus 2")

# ==============================================================================
# 4. HAUPTPROGRAMM (MENÜSTEUERUNG)
# ==============================================================================

if __name__ == "__main__":
    SIMULATION = False 
    
    VISA_MUX = 'USB0::2391::1287::MY65320033::0::INSTR'
    VISA_SMU = 'USB0::10893::37633::MY61390350::0::INSTR' 
    PORT_KEITHLEY = '/dev/ttyUSB0'
    
    SONDEN_KANAELE = ["1001", "1002", "1003", "1004", "1005"]

    print("====================================================")
    print("      LANGMUIR-SONDEN MESSSTAND INITIALISIERUNG     ")
    print("====================================================")
    
    mux = None
    try:
        mux = KeysightMultiplexer(VISA_MUX, simulate=SIMULATION)
        smu = KeysightSMU(VISA_SMU, simulate=SIMULATION)
        k_dmm = KeithleyDMM(PORT_KEITHLEY, simulate=SIMULATION)
        
        mux.open_all()
        
        while True:
            print("\n--- HAUPTMENÜ ---")
            print("[1] Einfache Langmuirsonde messen (Nur SMU)")
            print("[2] Langmuirsonde mit Guardring messen (SMU + Keithley)")
            print("[3] Programm beenden")
            
            auswahl = input("Bitte Modus wählen (1-3): ").strip()
            if auswahl == "1":
                modus_1_einfache_sonde(mux, smu, SONDEN_KANAELE)
            elif auswahl == "2":
                modus_2_guardring_sonde(mux, smu, k_dmm, SONDEN_KANAELE)
            elif auswahl == "3":
                print("\nProgramm beendet. Auf Wiedersehen!")
                break
                
        k_dmm.close()

    except Exception as global_error:
        print(f"\n[CRITICAL] Schwerwiegender Fehler: {global_error}")
        if mux is not None:
            try:
                mux.open_all()
            except:
                pass