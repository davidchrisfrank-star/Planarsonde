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
    """Steuerung des Keysight 34923A/34980A Multiplexers mit ABus-GND-Sicherheit."""
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
                
                # Gerätezustand zurücksetzen (Aktiviert standardmäßig Break-Before-Make)
                self.instrument.write('*RST')
                self.instrument.write('*CLS')
                self.instrument.query("*OPC?")
            except Exception as e:
                print(f"[FEHLER] Verbindung zum Multiplexer fehlgeschlagen: {e}")
                raise

    def safe_switch_to_channel(self, active_channel, total_channels_list, abus_gnd_ch="1913"):
        """
        Schaltet den aktiven Kanal zur SMU. Dank Break-Before-Make (BBM) 
        und vorgeschaltetem Bus-Öffnen sind Kurzschlüsse ausgeschlossen.
        """
        gnd_channels = [ch for ch in total_channels_list if ch != active_channel]
        gnd_channels_str = "@" + ",".join(gnd_channels)
        
        if self.simulate:
            print(f"  [SIM MUX] BBM-SWITCH: Schalte {active_channel} auf SMU. Reste ({gnd_channels_str}) auf GND.")
            return

        try:
            # 1. BREAK: Zuerst die Analogbusse trennen, um Querschlüsse zu blockieren
            self.instrument.write("ROUTe:OPEN (@1911,1914)")
            self.instrument.write("ROUT:OPEN:ALL")
            self.instrument.query("*OPC?") # Warten bis alle Relais physisch offen sind
            
            # 2. MAKE: Aktiven Kanal auf den Hauptbus (SMU) legen
            self.instrument.write(f"ROUT:CLOS (@{active_channel})")
            
            # 3. Alle anderen Kanäle + das ABus-Relais für die restliche Erdung schließen
            if gnd_channels:
                self.instrument.write(f"ROUT:MULT:CLOS ({gnd_channels_str},{abus_gnd_ch})")
            self.instrument.query("*OPC?")
            
            print(f"  [SAFETY] Break-Before-Make erfolgreich. Kanal {active_channel} aktiv.")
        except Exception as e:
            print(f"  [CRITICAL] Fehler beim BBM-Routing: {e}")
            self.open_all()
            raise

    def open_all(self):
        """Sicherer Not-Aus: Trennt alle Kanäle und Analogbusse."""
        if self.simulate:
            print("  [SIM MUX] Not-Aus: Öffne ALLE Kanäle und Busse.")
        else:
            self.instrument.write("ROUTe:OPEN (@1911,1914)")
            self.instrument.write("ROUT:OPEN:ALL")
            self.instrument.query("*OPC?")

class KeysightSMU:
    """Steuerung der Keysight B2910BL SMU via PyVISA (Software-Sweep optimiert)."""
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
        """Bereitet die SMU auf das manuelle Setzen von Spannungswerten vor."""
        if self.simulate:
            print(f"  [SIM SMU] Initialisiere Spannungsmodus (Compliance: {compliance_current}A)")
            return
        
        self.instrument.write("*RST")
        self.instrument.write(":SOUR:FUNC:MODE VOLT")
        self.instrument.write(f":SENS:CURR:PROT {compliance_current}")
        self.instrument.write(":SENS:CURR:RANG 100e-6") 
        self.instrument.write(":SENS:CURR:NPLC 1")
        self.instrument.write("*CLS")

    def set_voltage(self, voltage):
        """Setzt die Ausgangsspannung auf einen konkreten Wert."""
        if self.simulate:
            print(f"  [SIM SMU] Setze Spannung auf {voltage} V")
        else:
            self.instrument.write(f":SOUR:VOLT {voltage}")

    def measure_current(self):
        """Liest den aktuellen Strom von der SMU."""
        if self.simulate:
            return 5e-6 
        else:
            res = self.instrument.query(":MEAS:CURR?")
            try:
                return float(res.split(',')[0])
            except ValueError:
                return 0.0

    def output_on(self):
        if not self.simulate: self.instrument.write(":OUTP ON")

    def output_off(self):
        if not self.simulate: self.instrument.write(":OUTP OFF")


class KeithleyDMM:
    """Steuerung des Keithley 2700 Multimeters via Serieller Schnittstelle (pyserial)."""
    def __init__(self, port="/dev/ttyUSB0", simulate=False):
        self.simulate = simulate
        self.port = port
        self.ser = None

        if self.simulate:
            print(f"[SIMULATION] Virtuelles Keithley DMM an {self.port} aktiviert.")
        else:
            try:
                self.ser = serial.Serial(
                    port=self.port,
                    baudrate=9600,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=3
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
                    time.sleep(0.5) 
                    
                    idn = self.ser.readline().decode("utf-8").strip()
                    if idn:
                        print(f"  Geräte-Info: {idn}")
                    else:
                        print("  Geräte-Info: [Keine Antwort auf *IDN? - Verbindung steht trotzdem]")
                        
            except Exception as e:
                print(f"[FEHLER] Verbindung zum Keithley fehlgeschlagen: {e}")
                raise

    def init_current_measurement(self):
        """Konfiguriert das Keithley für Gleichstrommessung im µA-Bereich."""
        if self.simulate:
            print("  [SIM DMM] Konfiguriere DC-Strommessung (fest auf 100µA-Bereich).")
            return
        
        self.ser.write(b"*RST\r\n")
        time.sleep(0.2)
        self.ser.write(b":FUNC 'CURR:DC'\r\n")
        self.ser.write(b":CURR:DC:RANG 100e-6\r\n")
        time.sleep(0.1)
        self.ser.write(b":INIT:CONT ON\r\n")
        time.sleep(0.2)
        self.ser.write(b"*CLS\r\n")

    def read_current(self):
        """Triggert aktiv eine frische Messung und filtert den Stromwert heraus."""
        if self.simulate:
            return 1.2e-6 
        
        try:
            self.ser.write(b":READ?\r\n")
            time.sleep(0.15) 
            raw_data = self.ser.readline().decode("utf-8").strip()
            
            if raw_data:
                erster_teil = raw_data.split(",")[0]
                zahl_als_text = ""
                for zeichen in erster_teil:
                    if zeichen.isdigit() or zeichen in [".", "-", "+", "E", "e"]:
                        zahl_als_text += zeichen
                return float(zahl_als_text)
            return 0.0
        except (ValueError, IndexError):
            return float('nan')

    def close(self):
        if not self.simulate and self.ser and self.ser.is_open:
            self.ser.close()
            print("  Keithley-Schnittstelle sauber geschlossen.")


# ==============================================================================
# 2. HILFSFUNKTIONEN & DATEN-HANDLING
# ==============================================================================

def sweep_abfrage():
    """Fragt den Benutzer nach den Sweep-Parametern (mit Schrittweite) und generiert die Spannungsliste."""
    print("\n--- SWEEP-PARAMETER FESTLEGEN ---")
    try:
        start = float(input("Startspannung (V) [z.B. -10]: "))
        stop = float(input("Stoppspannung (V) [z.B. 10]: "))
        schrittweite = float(input("Schrittgröße (V) [z.B. 1.0 oder 0.5]: "))
        
        if schrittweite <= 0:
            print("[FEHLER] Schrittgröße muss größer als 0 sein! Nutze Standard-Schrittweite (1.0 V).")
            schrittweite = 1.0
            
        spanne = stop - start
        punkte = int(round(abs(spanne) / schrittweite)) + 1
        
        if punkte < 2:
            print("[INFO] Spanne zu klein für diese Schrittweite. Setze auf Mindestmaß (2 Punkte).")
            punkte = 2
            
        spannungs_liste = [start + (stop - start) * i / (punkte - 1) for i in range(punkte)]
        print(f"[INFO] Generierte Messreihe mit {punkte} Punkten (reale Schrittweite: {abs(spannungs_liste[1]-spannungs_liste[0]):.3f} V)")
        return spannungs_liste
        
    except ValueError:
        print("[FEHLER] Ungültige Eingabe! Nutze Standard-Sweep (-5V bis +5V, 1V Schritte).")
        return [(-5.0 + 1.0 * i) for i in range(11)]


def save_to_csv(data, filename, mode_label):
    """Speichert die gesammelten Messdaten sauber in eine CSV-Datei."""
    try:
        with open(filename, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Zeitstempel", "Spannung_Soll_V", "Strom_Ist_A", "Modus"])
            writer.writerows(data)
        print(f"[ERFOLG] Daten für {mode_label} erfolgreich gespeichert unter: {filename}")
    except Exception as e:
        print(f"[FEHLER] Fehler beim Speichern der CSV-Datei: {e}")

# ==============================================================================
# 3. MESS-MODI (LOGIK)
# ==============================================================================

def modus_1_einfache_sonde(mux, smu, alle_kanaele):
    """Führt einen Sweep an einer einfachen Langmuirsonde durch (Mit hardwareseitiger BBM-Sicherheit)."""
    print("\n=== MODUS 1: EINFACHE LANGMUIRSONDE ===")
    print(f"Verfügbare Sonden-Kanäle: {alle_kanaele}")
    
    kanal_raw = input("Welcher Multiplexer-Kanal wird verwendet? (z.B. 1002): ").strip().replace("@", "")
    
    if kanal_raw not in alle_kanaele:
        print(f"[FEHLER] Kanal {kanal_raw} ist nicht in der Konfiguration definiert!")
        return
        
    spannungen = sweep_abfrage()
    csv_datei = f"langmuir_einfach_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    messdaten = []

    try:
        # 1. Hardware vorbereiten
        smu.init_software_sweep(compliance_current=0.005) 
        
        # 2. Sicherer Schaltvorgang mittels BBM
        mux.safe_switch_to_channel(active_channel=kanal_raw, total_channels_list=alle_kanaele)
        time.sleep(0.5)
        
        # 3. Erst jetzt SMU aktivieren
        smu.output_on()
        
        print("\nStarte Messreihe (Software-Sweep)...")
        for i, u_soll in enumerate(spannungen):
            smu.set_voltage(u_soll)
            time.sleep(0.1) 
            
            i_ist = smu.measure_current()
            zeit = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            print(f"[{i+1}/{len(spannungen)}] SMU: {u_soll:6.2f} V -> Strom: {i_ist:.6e} A")
            messdaten.append([zeit, u_soll, i_ist, "Einfache Sonde"])
            
    finally:
        # Sicherheitsabschaltung: SMU aus und Multiplexer komplett trennen
        print("\nBereinige Hardware-Zustand...")
        smu.output_off()
        mux.open_all()
        
    if messdaten:
        save_to_csv(messdaten, csv_datei, "Modus 1")


def modus_2_guardring_sonde(mux, smu, k_dmm, alle_kanaele):
    """Führt einen Sweep mit Guardring durch (Beide aktiv via BBM)."""
    print("\n=== MODUS 2: LANGMUIRSONDE MIT GUARDRING ===")
    print(f"Verfügbare Sonden-Kanäle: {alle_kanaele}")
    
    kanal_guard = input("Kanal für GUARDRING (z.B. 1001): ").strip().replace("@", "")
    kanal_platte = input("Kanal für MESSPLATTE (z.B. 1002): ").strip().replace("@", "")
    
    if (kanal_guard not in alle_kanaele) or (kanal_platte not in alle_kanaele):
        print("[FEHLER] Einer der Kanäle ist nicht in der Konfiguration definiert!")
        return
        
    spannungen = sweep_abfrage()
    csv_datei = f"langmuir_guardring_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    messdaten = []

    try:
        # 1. Hardware vorbereiten
        smu.init_software_sweep(compliance_current=0.010) 
        k_dmm.init_current_measurement()
        
        # 2. Sonderfall Guardring: Zwei Kanäle parallel via BBM schalten
        gnd_channels = [ch for ch in alle_kanaele if ch != kanal_guard and ch != kanal_platte]
        gnd_channels_str = "@" + ",".join(gnd_channels)
        
        print(f"  [SAFETY] Schalte {kanal_guard} & {kanal_platte} auf SMU. Reste ({gnd_channels_str}) auf ABus3-GND.")
        
        if mux.simulate:
            print("    [SIM MUX] Modus 2 Kanäle geschaltet.")
        else:
            # BREAK: Erst die Busse 1 & 4 sowie alle Relais öffnen
            mux.instrument.write("ROUTe:OPEN (@1911,1914)")
            mux.instrument.write("ROUT:OPEN:ALL")
            mux.instrument.query("*OPC?")
            
            # MAKE: Beide Messkanäle zur SMU verbinden
            mux.instrument.write(f"ROUT:CLOS (@{kanal_guard},{kanal_platte})")
            # Alle inaktiven Kanäle auf ABus3 (GND) legen
            if gnd_channels:
                mux.instrument.write(f"ROUT:MULT:CLOS ({gnd_channels_str},1913)")
            mux.instrument.query("*OPC?")

        time.sleep(0.5)
        smu.output_on()
        
        print("\nStarte präzisen Guardring-Sweep...")
        for i, u_soll in enumerate(spannungen):
            smu.set_voltage(u_soll)
            time.sleep(0.15) 
            
            i_platte = k_dmm.read_current()
            zeit = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            i_ua = i_platte * 1e6
            print(f"[{i+1}/{len(spannungen)}] SMU: {u_soll:6.2f} V -> DMM (Platte): {i_ua:8.3f} µA")
            
            messdaten.append([zeit, u_soll, i_platte, "Guardring Sonde"])
            
    finally:
        # Sicherheitsabschaltung: Alles aus und Multiplexer komplett trennen
        print("\nBereinige Hardware-Zustand...")
        smu.output_off()
        mux.open_all()
        
    if messdaten:
        save_to_csv(messdaten, csv_datei, "Modus 2")

# ==============================================================================
# 4. HAUPTPROGRAMM (MENÜSTEUERUNG)
# ==============================================================================

if __name__ == "__main__":
    # === KONFIGURATION & ADRESSEN ===
    SIMULATION = False 
    
    VISA_MUX = 'USB0::2391::1287::MY65320033::0::INSTR'
    VISA_SMU = 'USB0::10893::37633::MY61390350::0::INSTR' 
    PORT_KEITHLEY = '/dev/ttyUSB0'
    
    SONDEN_KANAELE = ["1001", "1002", "1003", "1004", "1005"]
    # ================================

    print("====================================================")
    print("      LANGMUIR-SONDEN MESSSTAND INITIALISIERUNG     ")
    print("====================================================")
    
    try:
        # Geräte instanziieren
        mux = KeysightMultiplexer(VISA_MUX, simulate=SIMULATION)
        smu = KeysightSMU(VISA_SMU, simulate=SIMULATION)
        k_dmm = KeithleyDMM(PORT_KEITHLEY, simulate=SIMULATION)
        
        # Sicherstellen, dass zu Beginn alles getrennt ist
        mux.open_all()
        
        # Hauptmenü-Schleife
        while True:
            print("\n--- HAUPTMENÜ ---")
            print("[1] Einfache Langmuirsonde messen (Nur SMU | Rest geerdet)")
            print("[2] Langmuirsonde mit Guardring messen (SMU + Keithley | Rest geerdet)")
            print("[3] Programm beenden")
            
            auswahl = input("Bitte Modus wählen (1-3): ").strip()
            
            if auswahl == "1":
                modus_1_einfache_sonde(mux, smu, SONDEN_KANAELE)
            elif auswahl == "2":
                modus_2_guardring_sonde(mux, smu, k_dmm, SONDEN_KANAELE)
            elif auswahl == "3":
                print("\nProgramm beendet. Auf Wiedersehen!")
                break
            else:
                print("[INFO] Ungültige Auswahl, bitte 1, 2 oder 3 eingeben.")
                
        # Verbindungen am Ende trennen
        k_dmm.close()

    except Exception as global_error:
        print(f"\n[CRITICAL] Schwerwiegender Fehler beim Systemstart: {global_error}")
        print("Bitte überprüfe die USB-Verbindungen und Adressen der Messgeräte.")