import pyvisa
import time
import csv
from datetime import datetime

class Multiplexer:
    def __init__(self, resource_address, simulate=False):
        self.simulate = simulate
        self.address = resource_address
        self.instrument = None

        if self.simulate:
            print(f"[SIMULATION] Virtuelles Gerät an {self.address}")
        else:
            try:
                rm = pyvisa.ResourceManager('@py')                      // öffnet die Ressourcenverwaltung mit pyvisa-py Backend
                self.instrument = rm.open_resource(self.address)        // öffnet die Verbindung zum Gerät
                self.instrument.timeout = 5000                          // setzt die Timeout-Zeit auf 5 Sekunden zur Vermeidung von Hängern
                idn = self.instrument.query('*IDN?').strip()            // fragt die Geräteidentifikation ab und entfernt überflüssige Leerzeichen
                print(f"[ERFOLG] Verbunden mit: {idn}")
                self.instrument.write('*RST')                           // setzt das Gerät in den Ausgangszustand zurück
                self.instrument.write('*CLS')                           // löscht alle Fehler und Statusregister, um mit einem sauberen Zustand zu starten
            except Exception as e:                                      // fängt alle Fehler ab, die beim Verbindungsaufbau auftreten können
                print(f"[FEHLER] {e}")
                raise                                                   // gibt den Fehler weiter, damit er im Hauptprogramm behandelt werden kann
                

    def send(self, command):                                            // Methode zum Senden von Befehlen an das Gerät oder zur Simulation der Ausgabe
        if self.simulate:
            print(f"  [SIM TX] {command}")
        else:
            self.instrument.write(command)

    def query(self, command):                                           // Methode zum Abfragen von Werten vom Gerät oder zur Simulation der Ausgabe
        if self.simulate:
            return "1.2345"
        else:
            return self.instrument.query(command).strip()               // sendet den Befehl und gibt die Antwort zurück, wobei überflüssige Leerzeichen entfernt werden

    def close_channel(self, channel_list):                              // Methode zum Schließen von Kanälen, entweder durch Senden des Befehls oder durch Simulation der Ausgabe
        print(f"Schließe {channel_list}...")
        self.send(f"ROUT:CLOS ({channel_list})")

    def open_all(self):                                                 // Methode zum Öffnen aller Kanäle, entweder durch Senden des Befehls oder durch Simulation 
        print("Öffne ALLE Kanäle...")                                   // der Ausgabe, Öffnet alle Kanäle, um sicherzustellen, dass keine unerwünschten Verbindungen 
        self.send("ROUT:OPEN:ALL")                                      // bestehen bleiben (Schütz vor ungewollten Spannungen)

    def measure_dc_voltage(self, channel_list):                         // Methode zum Messen der Gleichspannung an den angegebenen Kanälen, entweder durch Senden des Befehls oder durch Simulation der Ausgabe
        return self.query(f"MEAS:VOLT:DC? ({channel_list})")            // MEAS:VOLT:DC? ist der SCPI-Befehl zur Abfrage der Gleichspannung, die Antwort wird zurückgegeben. Das Fragezeichen zwingt das Gerät zu antworten.
                                                                        
                                                            
if __name__ == "__main__":                                              // Hauptprogramm, das die Multiplexer-Klasse verwendet, um Messwerte zu erfassen und in einer CSV-Datei zu speichern
    VISA_ADDRESS = 'USB0::2391::1287::MY65320033::0::INSTR'             // Beispieladresse, muss an die tatsächliche Adresse des Geräts angepasst werden. Diese kann mit einem VISA-Scanner ermittelt werden.
    SIMULATION = False 
    CSV_DATEI = "messungen.csv"

    # CSV-Header schreiben (überschreibt alte Datei gleichen Namens)
    with open(CSV_DATEI, mode='w', newline='') as file:                 // öffnet die CSV-Datei im Schreibmodus, wodurch alte Daten gelöscht werden. newline='' verhindert zusätzliche Zeilenumbrüche in der CSV-Datei
        writer = csv.writer(file)
        writer.writerow(["Zeitstempel", "Kanal", "Spannung_V"])         // schreibt die Spaltenüberschriften in die CSV-Datei

    mux = Multiplexer(VISA_ADDRESS, simulate=SIMULATION)

    try:
        mux.open_all()
        time.sleep(1)

        # Eine kleine Schleife, um 5 Messwerte im Abstand von 1 Sekunde aufzunehmen
        print("\nStarte Messreihe (5 Messungen)...")
        for i in range(5):
            spannung = mux.measure_dc_voltage('@1002')                  // misst die Gleichspannung am Kanal @1002, der SCPI-Befehl gibt den Wert zurück, der in der Variable 'spannung' gespeichert wird
            zeit = datetime.now().strftime("%Y-%m-%d %H:%M:%S")         // erfasst den aktuellen Zeitstempel im Format "Jahr-Monat-Tag Stunde:Minute:Sekunde", um die Messung zeitlich zu dokumentieren
            
            # Wert in die Konsole drucken
            print(f"[{zeit}] Wert {i+1}: {spannung} V")
            
            # Wert in die CSV-Datei anfügen (mode='a' für append)
            with open(CSV_DATEI, mode='a', newline='') as file:         // öffnet die CSV-Datei im Anhängemodus, um neue Daten hinzuzufügen, ohne alte Daten zu löschen. newline='' verhindert zusätzliche Zeilenumbrüche in der CSV-Datei
                writer = csv.writer(file)
                writer.writerow([zeit, "@1002", spannung])              // schreibt den Zeitstempel, den Kanal und die gemessene Spannung als neue Zeile in die CSV-Datei
            
            time.sleep(1) # 1 Sekunde bis zur nächsten Messung warten

    finally:
        print("\nBeende Programm, setze Multiplexer zurück...")
        mux.open_all()
        print(f"Alle Daten wurden in '{CSV_DATEI}' gespeichert.")
