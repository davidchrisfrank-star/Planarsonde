import os
import datetime
import numpy as np
from scipy.signal import savgol_filter
from scipy.interpolate import interp1d
from scipy.stats import linregress
import matplotlib.pyplot as plt

class LangmuirAnalyzer:
    """
    Klassische Plasma-Physik-Engine zur mathematischen Reduktion von
    Kennliniendaten einer planaren Langmuir-Sonde.
    """
    def __init__(self, voltages, currents):
        self.voltages = np.array(voltages)
        self.currents = np.array(currents)
        
        # Ergebnisvariablen
        self.v_fl = None
        self.v_p = None
        self.i_ion_sat = None
        self.t_e = None
        self.i_e_sat = None
        
        # Gefilterte Daten
        self.i_smooth = None
        self.dI_dV = None

    def process_data(self):
        """Führt alle physikalischen Auswertungen sequenziell aus."""
        # 1. Preprocessing (Savitzky-Golay Filter)
        # Fenstergröße dynamisch anpassen, muss ungerade sein
        window = max(5, int(len(self.voltages) * 0.02) | 1)
        self.i_smooth = savgol_filter(self.currents, window_length=window, polyorder=3)

        # 2. Floating Potential (V_fl) dort wo I = 0
        self._calculate_floating_potential()

        # 3. Differentieller Leitwert & Plasmapotential (V_p)
        self._calculate_plasma_potential()

        # 4. Ionen-Sättigungsstrom (Fit im stark Negativen)
        self._analyze_ion_saturation()

        # 5. Elektronentemperatur (T_e)
        self._analyze_electron_temperature()

        return {
            "V_fl [V]": self.v_fl,
            "V_p [V]": self.v_p,
            "I_ion_sat [A]": self.i_ion_sat,
            "T_e [eV]": self.t_e
        }

    def _calculate_floating_potential(self):
        """Bestimmt das Floating Potential mittels linearer Interpolation der Nullstelle."""
        zero_crossings = np.where(np.diff(np.sign(self.i_smooth)))[0]
        if len(zero_crossings) > 0:
            idx = zero_crossings[0]
            v_bracket = self.voltages[idx:idx+2]
            i_bracket = self.i_smooth[idx:idx+2]
            interp = interp1d(i_bracket, v_bracket, kind='linear')
            self.v_fl = float(interp(0.0))
        else:
            # Fallback falls asymmetrischer Sweep
            self.v_fl = float(self.voltages[np.argmin(np.abs(self.i_smooth))])

    def _calculate_plasma_potential(self):
        """Berechnet dI/dV. Das Maximum definiert das Plasmapotential V_p."""
        self.dI_dV = np.gradient(self.i_smooth, self.voltages)
        max_idx = np.argmax(self.dI_dV)
        self.v_p = float(self.voltages[max_idx])

    def _analyze_ion_saturation(self):
        """Lineare Extrapolation des Ionenstroms im Bereich -50V bis -30V."""
        ion_zone = (self.voltages >= -50.0) & (self.voltages <= -30.0)
        
        # Fallback falls Sweep-Bereich kleiner ist
        if np.sum(ion_zone) < 5:
            ion_zone = self.voltages < (self.v_fl - 5.0)

        slope, intercept, _, _, _ = linregress(self.voltages[ion_zone], self.i_smooth[ion_zone])
        
        # I_sat definiert als der Fit-Wert evaluiert bei V_fl
        self.i_ion_fit_func = lambda v: slope * v + intercept
        self.i_ion_sat = float(self.i_ion_fit_func(self.v_fl))

    def _analyze_electron_temperature(self):
        """Subtrahiert den Ionenstrom und berechnet T_e über die Steigung im Übergangsbereich."""
        # Reine Elektronenstromkomponente extrahieren
        i_ion_fitted = self.i_ion_fit_func(self.voltages)
        i_electron = self.i_smooth - i_ion_fitted
        
        # Lokalisierung des exponentiellen Übergangsbereichs [V_fl, V_p]
        te_zone = (self.voltages > self.v_fl) & (self.voltages < self.v_p) & (i_electron > 0)
        
        if np.sum(te_zone) > 3:
            ln_i_e = np.log(i_electron[te_zone])
            slope, intercept, _, _, _ = linregress(self.voltages[te_zone], ln_i_e)
            
            self.t_e = 1.0 / slope if slope != 0 else np.nan
            self.i_e_fit_func = lambda v: np.exp(slope * v + intercept)
            self.te_zone_data = (self.voltages[te_zone], ln_i_e)
        else:
            self.t_e = np.nan
            self.te_zone_data = (None, None)

    def generate_plots(self, save_path=None):
        """Erstellt die normgerechte physikalische Visualisierung mit Ergebnisfeld."""
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

        # Plot 1: Gesamte Kennlinie + Ionen-Fit
        ax1.plot(self.voltages, self.currents * 1e3, 'gray', alpha=0.5, label='Rohdaten')
        ax1.plot(self.voltages, self.i_smooth * 1e3, 'b-', lw=2, label='Savitzky-Golay')
        
        v_fit_arr = np.linspace(self.voltages[0], self.v_fl, 100)
        ax1.plot(v_fit_arr, self.i_ion_fit_func(v_fit_arr) * 1e3, 'r--', label='Ionen-Sättigungs-Fit')
        
        ax1.axvline(self.v_fl, color='g', linestyle=':', label=f'V_fl ({self.v_fl:.1f} V)')
        ax1.axvline(self.v_p, color='m', linestyle=':', label=f'V_p ({self.v_p:.1f} V)')
        
        ax1.set_xlabel('Sondenspannung V_s (V)')
        ax1.set_ylabel('Sondenstrom I_s (mA)')
        ax1.set_title('I(V) Charakteristik')
        ax1.grid(True)
        ax1.legend()

        # Plot 2: Logarithmischer Elektronenstrom für T_e
        i_electron = self.i_smooth - self.i_ion_fit_func(self.voltages)
        valid = i_electron > 0
        
        ax2.plot(self.voltages[valid], np.log(i_electron[valid]), 'b.', label='ln(I_e)')
        if self.te_zone_data[0] is not None:
            v_zone, ln_i_zone = self.te_zone_data
            ax2.plot(v_zone, ln_i_zone, 'r-', lw=2, label='Linearer Fit (T_e)')
            
        ax2.set_xlabel('Sondenspannung V_s (V)')
        ax2.set_ylabel('ln(I_e)')
        ax2.set_title('Elektronen-Energie-Verteilung')
        ax2.grid(True)
        ax2.legend()

        # Textbox Infofeld einbetten
        textstr = '\n'.join((
            r'$V_{fl} = %.2f\ \mathrm{V}$' % (self.v_fl,),
            r'$V_p = %.2f\ \mathrm{V}$' % (self.v_p,),
            r'$I_{ion,sat} = %.3f\ \mathrm{mA}$' % (self.i_ion_sat * 1e3,),
            r'$T_e = %.2f\ \mathrm{eV}$' % (self.t_e,)
        ))
        props = dict(boxstyle='round', facecolor='wheat', alpha=0.5)
        ax1.text(0.05, 0.95, textstr, transform=ax1.transAxes, fontsize=11,
                 verticalalignment='top', bbox=props)

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300)
        plt.show()

    def export_to_csv(self, base_dir="outputs"):
        """Automatisierter Export im zeitsynchronisierten CSV-Format."""
        if not os.path.exists(base_dir):
            os.makedirs(base_dir)
            
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(base_dir, f"langmuir_data_{timestamp}.csv")
        
        # Speichern der Rohdaten und glatten Ströme
        with open(filename, 'w') as f:
            f.write("# Langmuir Probe Analysis Report\n")
            f.write(f"# Timestamp: {timestamp}\n")
            f.write(f"# Floating Potential (V_fl): {self.v_fl:.4f} V\n")
            f.write(f"# Plasma Potential (V_p): {self.v_p:.4f} V\n")
            f.write(f"# Ion Saturation Current: {self.i_ion_sat:.6e} A\n")
            f.write(f"# Electron Temperature (T_e): {self.t_e:.4f} eV\n")
            f.write("Voltage(V),Current_Raw(A),Current_Smooth(A)\n")
            for v, i_r, i_s in zip(self.voltages, self.currents, self.is_smooth if self.i_smooth is not None else self.currents):
                f.write(f"{v:.4f},{i_r:.6e},{i_s:.6e}\n")
                
        print(f"[+] Datensatz erfolgreich exportiert nach: {filename}")