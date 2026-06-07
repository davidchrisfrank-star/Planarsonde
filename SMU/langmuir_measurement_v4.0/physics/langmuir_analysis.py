"""
Physikalische Auswertungs-Engine für die Langmuirsonde
=======================================================
Implementiert die Standard-Einzel-Langmuirsonden-Analyse für eine
planare Sonde im Dünnschicht/OML-Regime.

Analyse-Pipeline (in dieser Reihenfolge):
  1. Glättung (Savitzky-Golay / Gauß / Gleitender Mittelwert / Median)
  2. Schwebepotential  V_fl   → Nulldurchgang des geglätteten Stroms
  3. Differentielle Leitfähigkeit dI/dV  → np.gradient
  4. Plasmapotential   V_p    → Maximum von dI/dV
  5. Ionensättigungsfit I_ion(V) → Linearer Fit im stark negativen Bias-Bereich
  6. Elektronentemperatur T_e [eV] → Steigung von ln(I_e) vs V in [V_fl, V_p]
  7. Elektronensättigung I_e_sat  → Linearer Fit für V > V_p (Scheidenexpansion)

Verfügbare Filtermethoden:
  "savgol"   – Savitzky-Golay (Standard, bewahrt Kurvenform am besten)
  "gaussian" – Gauß-Faltung (sanfte Glättung)
  "moving"   – Einfacher gleitender Mittelwert (Box-Kernel)
  "median"   – Medianfilter (robust gegen Einzelausreißer/Spikes)
  "none"     – Keine Filterung (Rohdaten direkt verwenden)

Physikalischer Hintergrund:
  Im Übergangsbereich (V_fl ≤ V ≤ V_p) folgt der Elektronenstrom einer
  retardierten Maxwell-Verteilung:
      I_e(V) = I_e_sat · exp((V − V_p) / T_e)
  Der natürliche Logarithmus ergibt eine Gerade mit Steigung 1/T_e [eV⁻¹].

Literatur:
  Lieberman & Lichtenberg, "Principles of Plasma Discharges", 2nd ed., §2.3
  Merlino, Am. J. Phys. 75, 1078 (2007)
"""

from __future__ import annotations

import warnings
import numpy as np
from scipy.signal import savgol_filter, butter, filtfilt  # type: ignore
from scipy.ndimage import gaussian_filter1d, median_filter, uniform_filter1d  # type: ignore
from dataclasses import dataclass
from typing import Optional, Tuple

# Gültige Filterbezeichnungen – wird auch von der GUI importiert
FILTER_METHODS = ("savgol", "gaussian", "moving", "median", "butterworth", "spike_savgol", "none")


# ====================================================================== #
#  Ausnahme-Klasse
# ====================================================================== #

class AnalysisError(RuntimeError):
    """Wird ausgelöst wenn die physikalische Analyse kein sinnvolles Ergebnis liefern kann."""


# ====================================================================== #
#  Ergebnis-Datenklasse
# ====================================================================== #

@dataclass
class LangmuirResults:
    """
    Behälter für alle extrahierten Plasmaparameter.

    Alle Ströme in Ampere, alle Spannungen in Volt, T_e in eV.
    """
    V_fl:        float           # Schwebepotential (V)  – wo der Nettostrom = 0 ist
    V_p:         float           # Plasmapotential (V)   – wo dI/dV maximal ist
    T_e:         float           # Elektronentemperatur (eV)
    I_ion_sat:   float           # Ionensättigungsstrom (A, < 0)
    I_e_sat:     float           # Elektronensättigungsstrom (A, > 0)
    poly_ion:    np.ndarray      # Koeffizienten des linearen Ionensättigungs-Fits [Steigung, Achsenabschnitt]
    poly_esat:   Optional[np.ndarray]  # Koeffizienten des Elektronensättigungs-Fits (None wenn < 3 Punkte)
    poly_te:     np.ndarray      # Koeffizienten des ln(I_e)-Fits für T_e-Bestimmung
    ln_Ie:       np.ndarray      # ln(Elektronenstrom) im Übergangsbereich
    V_te_region: np.ndarray      # Spannungspunkte die für den T_e-Fit verwendet wurden

    def als_dict(self) -> dict:
        """Gibt die skalaren Ergebnisse als einfaches Dictionary zurück (ohne Arrays)."""
        return {
            "V_fl_V":      self.V_fl,
            "V_p_V":       self.V_p,
            "T_e_eV":      self.T_e,
            "I_ion_sat_A": self.I_ion_sat,
            "I_e_sat_A":   self.I_e_sat,
        }

    # Alias für Rückwärtskompatibilität
    def as_dict(self) -> dict:
        return self.als_dict()

    def zusammenfassung(self) -> str:
        """Gibt eine lesbare Zusammenfassung der Ergebnisse zurück."""
        zeilen = [
            "=" * 40,
            "  Langmuir-Sonden Analyseergebnisse",
            "=" * 40,
            f"  Schwebepotential   V_fl   = {self.V_fl:+.3f} V",
            f"  Plasmapotential    V_p    = {self.V_p:+.3f} V",
            f"  Elektronentemp.   T_e    =  {self.T_e:.3f} eV",
            f"  Ionensättig.-Str. I_ion  =  {self.I_ion_sat * 1e3:.3f} mA",
            f"  Elektronen-Sät.   I_e_sat=  {self.I_e_sat * 1e3:.3f} mA",
            "=" * 40,
        ]
        return "\n".join(zeilen)

    # Alias für Rückwärtskompatibilität (GUI ruft summary() auf)
    def summary(self) -> str:
        return self.zusammenfassung()


# ====================================================================== #
#  Haupt-Analyserklasse
# ====================================================================== #

class LangmuirAnalyzer:
    """
    Schritt-für-Schritt Physikanalyse einer planaren Langmuirsonden-I(V)-Kennlinie.

    Parameter
    ---------
    V : array-like
        Spannungsarray (V), monoton steigend.
    I : array-like
        Gemessenes Stromarray (A), gleiche Länge wie V.
    savgol_window : int
        Filterfensterlänge (wird von allen Filtermethoden verwendet).
        Größer = glatter, aber Gefahr der Verschmierung scharf Merkmale nahe V_p.
        **Faustregel:** savgol_window × dV ≤ T_e (in eV).
        Bei typischen T_e = 2–5 eV und dV = 0,1 V/Punkt → w = 21–51.
        Standard 21 ist gut für 1000-Punkte-Sweeps über 100 V (dV = 0,1 V/Punkt).
        Bei engerem Sweep (dV kleiner) kann w größer gewählt werden.
    savgol_polyorder : int
        Polynomgrad für den Savitzky-Golay-Filter. Standard 3.
    ion_fit_range : (float, float)
        Spannungsbereich (V_min, V_max) für den Ionensättigungsfit.
        Sollte tief im Ionensättigungsplateau liegen – deutlich unterhalb von V_fl.
        Standard (-50, -30) V; wird automatisch auf den tatsächlichen Sweep-Bereich begrenzt.
    filter_method : str
        Eine der Methoden aus FILTER_METHODS (Standard: "savgol").
          savgol       – Savitzky-Golay (Standard, Kurvenform + Ableitungen erhalten)
          gaussian     – Gauß-Faltung (sanft, kein endliches Fenster)
          moving       – Gleitender Mittelwert (einfach, Reflect-Padding)
          median       – Medianfilter (Reflect-Padding, robust gegen Spikes)
          butterworth  – Butterworth-Tiefpass 4. Ordnung (nullphasig via filtfilt)
          spike_savgol – Zweistufig: Medianfilter (Spike-Entfernung) + SavGol
          none         – Keine Filterung
    """

    # Stromauflösung des B2910BL (10 fA) – wird als Rauschgrenze für ln(I_e) verwendet
    RAUSCHGRENZE: float = 10e-15  # A

    # Alias für Rückwärtskompatibilität
    CURR_NOISE_FLOOR: float = 10e-15

    def __init__(
        self,
        V: np.ndarray,
        I: np.ndarray,
        savgol_window:    int   = 21,
        savgol_polyorder: int   = 3,
        ion_fit_range:    Tuple[float, float] = (-50.0, -30.0),
        filter_method:    str   = "savgol",
    ) -> None:
        self.V               = np.asarray(V, dtype=float)
        self.I               = np.asarray(I, dtype=float)
        self.savgol_window   = savgol_window
        self.savgol_polyorder = savgol_polyorder
        self.ion_fit_range   = ion_fit_range

        # Filtermethode validieren
        if filter_method not in FILTER_METHODS:
            raise ValueError(
                f"filter_method muss einer von {FILTER_METHODS} sein, erhalten: '{filter_method}'"
            )
        self.filter_method = filter_method

        # Array-Dimensionen prüfen
        if self.V.shape != self.I.shape:
            raise ValueError(
                f"V und I müssen dieselbe Form haben: {self.V.shape} vs {self.I.shape}"
            )
        if len(self.V) < 10:
            raise ValueError("Zu wenige Datenpunkte – mindestens 10 für die Analyse benötigt.")

        # Interne Arbeitsarrays – werden schrittweise in analyze() befüllt
        self.I_smooth:   Optional[np.ndarray] = None
        self.dIdV:       Optional[np.ndarray] = None
        self.I_electron: Optional[np.ndarray] = None

    # ================================================================== #
    #  Öffentliche Schnittstelle
    # ================================================================== #

    def analyze(self) -> LangmuirResults:
        """
        Die vollständige Analyse-Pipeline ausführen und ein LangmuirResults-Objekt zurückgeben.

        Die Schritte sind nummeriert entsprechend dem Modul-Docstring.

        Rückgabe
        --------
        LangmuirResults
            Alle extrahierten Plasmaparameter plus Zwischen-Arrays für die Darstellung.

        Ausnahmen
        ---------
        AnalysisError
            Falls ein physikalisch sinnvolles Ergebnis nicht extrahiert werden kann.
        """
        # Schritt 1 – Glättung
        self.I_smooth = self._glaetten()

        # Schritt 2 – Schwebepotential
        V_fl = self._schwebepotential_finden(self.I_smooth)

        # Schritt 3 & 4 – dI/dV und Plasmapotential
        self.dIdV = self._ableitung_berechnen(self.I_smooth)
        V_p = self._plasmapotential_finden(self.dIdV, V_fl)

        # Schritt 5 – Ionensättigungsstrom
        poly_ion, I_ion_sat = self._ionensaettigung_fitten(self.I_smooth, V_fl)

        # Schritt 6 – Elektronentemperatur
        # Ionenstromanteil subtrahieren um reinen Elektronenstrom zu erhalten
        self.I_electron = self.I_smooth - np.polyval(poly_ion, self.V)
        poly_te, T_e, ln_Ie, V_te = self._elektronentemperatur_fitten(
            self.I_electron, V_fl, V_p
        )

        # Schritt 7 – Elektronensättigungsstrom
        poly_esat, I_e_sat = self._elektronensaettigung_fitten(self.I_smooth, V_p)

        return LangmuirResults(
            V_fl=V_fl,
            V_p=V_p,
            T_e=T_e,
            I_ion_sat=I_ion_sat,
            I_e_sat=I_e_sat,
            poly_ion=poly_ion,
            poly_esat=poly_esat,
            poly_te=poly_te,
            ln_Ie=ln_Ie,
            V_te_region=V_te,
        )

    # ================================================================== #
    #  Pipeline-Schritte – private Methoden
    # ================================================================== #

    def _glaetten(self) -> np.ndarray:
        """
        Schritt 1 – Ausgewählten Filter auf den Strom-Array anwenden.

        Filtermethoden:
          savgol       – Savitzky-Golay: Polynomfit in überlappenden Fenstern,
                         bewahrt Kurvenform und Ableitungen (bevorzugt für V_p)
          gaussian     – Gauß-Faltung: sigma ≈ w/6 (3-Sigma-Näherung).
                         Physikalische Glättungsbreite: sigma_V = (w/6) × dV Volt.
          moving       – Gleichmäßiger gleitender Mittelwert mit Reflect-Padding.
                         uniform_filter1d statt np.convolve verhindert Randwert-Artefakte.
          median       – Medianfilter mit Reflect-Padding: robust gegen isolierte Spikes.
                         WICHTIG: mode='reflect' verhindert ~15 % Ionenfit-Fehler durch
                         Zero-Padding, der bei scipy.signal.medfilt auftreten würde.
          butterworth  – Nullphasiger Butterworth-Tiefpass 4. Ordnung (via filtfilt).
                         Cutoff: fc = 2/w. Mathematisch scharfe Frequenztrennung.
          spike_savgol – Zweistufig: Medianfilter (w=7) zur Spike-Entfernung,
                         dann SavGol für Kurvenformerhaltung. Optimal für reale
                         Labordaten mit elektromagnetischen Störungen/Spikes.
          none         – Keine Filterung, Rohdaten unverändert weitergeben.

        Warnung: savgol_window × dV sollte ≤ T_e (in eV) sein.
        Bei zu breitem Fenster wird der dI/dV-Peak verbreitert → V_p-Verschiebung.
        Empfehlung: w = 21 für 1000-Punkte-Sweep über 100 V (dV = 0,1 V/Punkt).
        """
        n  = len(self.I)
        w  = self.savgol_window
        dV = (self.V[-1] - self.V[0]) / max(n - 1, 1)

        # Keine Filterung gewünscht
        if self.filter_method == "none":
            return self.I.copy()

        # Fensterlänge auf gültige ungerade Zahl klemmen
        w = min(w, n if n % 2 == 1 else n - 1)      # Datenlänge nicht überschreiten
        w = max(w, self.savgol_polyorder + 1)         # scipy-Anforderung: window > polyorder
        if w % 2 == 0:
            w += 1                                     # Ungerade Fensterlänge erzwingen

        # Warnung wenn Fenster zu breit für typische T_e-Werte ist
        if w * abs(dV) > 5.0 and self.filter_method not in ("none", "butterworth"):
            warnings.warn(
                f"Filterfenster w={w} × dV={dV:.3f} V = {w * abs(dV):.1f} V erscheint "
                "zu breit für typische T_e-Werte (2–5 eV). "
                "V_p kann systematisch verschoben sein. Fenster auf ≤ 31 reduzieren.",
                stacklevel=3,
            )

        if self.filter_method == "savgol":
            return savgol_filter(self.I, w, self.savgol_polyorder)

        if self.filter_method == "gaussian":
            # Sigma = w/6: 3σ ≈ halbe Fensterlänge → 99,7 % der Gaußkurve im Fenster.
            # mode='reflect' verhindert Randwert-Artefakte.
            sigma = max(w / 6.0, 1.0)
            return gaussian_filter1d(self.I, sigma=sigma, mode="reflect")

        if self.filter_method == "moving":
            # uniform_filter1d mit mode='nearest': Randpunkte werden gehalten statt
            # auf 0 zu fallen. np.convolve(mode='same') würde bis zu 49 % Bias
            # am ersten Punkt erzeugen (gemessen bei w=51).
            return uniform_filter1d(self.I, size=w, mode="nearest")

        if self.filter_method == "median":
            # mode='reflect': Signal wird am Rand gespiegelt.
            # scipy.signal.medfilt würde Zero-Padding verwenden und den
            # Ionensättigungs-Slope um ~15 % verfälschen.
            return median_filter(self.I, size=w, mode="reflect")

        if self.filter_method == "butterworth":
            # Nullphasiger Butterworth-Tiefpass 4. Ordnung (via filtfilt = vorwärts + rückwärts).
            # Cutoff fc = 2/w (normiert auf Nyquist-Frequenz 0–1).
            fc = float(np.clip(2.0 / w, 1e-4, 0.99))
            b, a = butter(4, fc, btype="low")
            return filtfilt(b, a, self.I)

        if self.filter_method == "spike_savgol":
            # Schritt 1: Kleines Median-Fenster (w=7) entfernt isolierte Spikes
            #            ohne die Kurvenform wesentlich zu verändern.
            I_ohne_spikes = median_filter(self.I, size=min(7, w), mode="reflect")
            # Schritt 2: Savitzky-Golay bewahrt Kurvenform und Ableitungen für V_p.
            return savgol_filter(I_ohne_spikes, w, self.savgol_polyorder)

        # Fallback (sollte wegen der __init__-Prüfung nie erreicht werden)
        return savgol_filter(self.I, w, self.savgol_polyorder)

    def _schwebepotential_finden(self, I_s: np.ndarray) -> float:
        """
        Schritt 2 – Schwebepotential: Spannung bei der der Nettostrom I = 0 ist.

        Das Schwebepotential ist der Sonden-Bias, bei dem sich Elektronen- und
        Ionenstrom genau aufheben. Gefunden durch lineare Interpolation zwischen
        den benachbarten Punkten, die den Nulldurchgang einrahmen.

        Bei mehreren Nulldurchgängen (z. B. Rauschartefakte bei sehr negativem Bias)
        wird der Durchgang gewählt, der am nächsten zur Mitte des Spannungs-Sweeps liegt,
        da dort der physikalische Übergang stattfindet.
        """
        vorzeichen = np.sign(I_s)
        # Positionen finden wo sich das Vorzeichen ändert
        durchgaenge = np.where(np.diff(vorzeichen) != 0)[0]

        if len(durchgaenge) == 0:
            raise AnalysisError(
                "Kein Nulldurchgang in I(V) gefunden. "
                "Sweep-Bereich erweitern, so dass Ionensättigung UND "
                "Elektronensättigung erfasst werden."
            )

        # Den Durchgang der der Sweep-Mitte am nächsten liegt bevorzugen
        v_mitte = 0.5 * (self.V[0] + self.V[-1])
        idx = durchgaenge[np.argmin(np.abs(self.V[durchgaenge] - v_mitte))]

        # Lineares Interpolieren zwischen den einrahmenden Punkten
        V_fl = self.V[idx] - I_s[idx] * (
            (self.V[idx + 1] - self.V[idx]) / (I_s[idx + 1] - I_s[idx])
        )
        return float(V_fl)

    def _ableitung_berechnen(self, I_s: np.ndarray) -> np.ndarray:
        """
        Schritt 3 – Differentielle Leitfähigkeit dI/dV.

        Verwendet das zentrale Differenzenverfahren zweiter Ordnung von numpy.
        Die Leitfähigkeitskurve wird zur Bestimmung von V_p (ihr Maximum)
        und als Qualitätsdiagnostikum für die Messkurve verwendet.
        """
        return np.gradient(I_s, self.V)

    def _plasmapotential_finden(self, dIdV: np.ndarray, V_fl: float) -> float:
        """
        Schritt 4 – Plasmapotential: Spannung am Maximum von dI/dV.

        Physikalische Begründung: Bei V = V_p verschwindet die elektrostatische
        Barriere für Elektronen. Die Elektronensammelrate ändert sich hier am
        stärksten, was zum Leitfähigkeitspeak führt.

        Die Suche wird auf V ≥ V_fl beschränkt, um Scheinpeaks im
        Ionensättigungsplateau (durch Rauschen) zu vermeiden.
        """
        maske = self.V >= V_fl
        if maske.sum() < 3:
            # Sonderfall: V_fl liegt nahe am Ende des Sweeps
            warnings.warn(
                "V_fl liegt nahe an der Sweep-Grenze; V_p-Suche verwendet den gesamten Bereich.",
                stacklevel=3,
            )
            maske = np.ones(len(self.V), dtype=bool)

        # Maximum von dI/dV im erlaubten Bereich finden
        lokaler_peak_idx = int(np.argmax(dIdV[maske]))
        globaler_idx     = np.where(maske)[0][lokaler_peak_idx]
        return float(self.V[globaler_idx])

    def _ionensaettigung_fitten(
        self, I_s: np.ndarray, V_fl: float
    ) -> Tuple[np.ndarray, float]:
        """
        Schritt 5 – Ionensättigungsstrom über linearen Fit.

        Weit unterhalb von V_fl werden alle Elektronen abgestosssen; nur Ionen
        erreichen die Sonde. Ein linearer Fit berücksichtigt die leichte Steigung
        durch Scheidenexpansion (Sondensammelfläche wächst mit zunehmendem
        negativem Bias). Der Fit wird bis V_fl extrapoliert, um den Ionenstromanteil
        am Schwebepunkt zu erhalten.
        """
        v_min, v_max = self.ion_fit_range

        # Angeforderten Bereich auf den tatsächlichen Sweep-Bereich begrenzen
        v_min = max(v_min, self.V.min())
        # Nicht über V_fl hinausgehen – Elektronenstrom würde den Fit verfälschen
        v_max = min(v_max, V_fl - 2.0, self.V.max())
        if v_max <= v_min:
            # Fallback: unterste 20 % des Spannungsbereichs
            v_max = self.V.min() + 0.2 * (self.V.max() - self.V.min())

        maske = (self.V >= v_min) & (self.V <= v_max)
        if maske.sum() < 3:
            # Letzter Ausweg: die 10 negativsten Spannungspunkte verwenden
            maske = np.zeros(len(self.V), dtype=bool)
            maske[: min(10, len(self.V))] = True

        # Linearer Fit (Polynom Grad 1) im Ion-Sättigungsbereich
        poly_ion      = np.polyfit(self.V[maske], I_s[maske], 1)
        I_ion_bei_Vfl = float(np.polyval(poly_ion, V_fl))
        return poly_ion, I_ion_bei_Vfl

    def _elektronentemperatur_fitten(
        self,
        I_electron: np.ndarray,
        V_fl: float,
        V_p: float,
    ) -> Tuple[np.ndarray, float, np.ndarray, np.ndarray]:
        """
        Schritt 6 – Elektronentemperatur aus der Steigung von ln(I_e).

        Im Maxwell'schen Übergangsbereich [V_fl, V_p]:
            I_e(V) = I_e_sat · exp((V − V_p) / T_e)
        ⟹  ln(I_e) = V / T_e + const

        Ein linearer Fit an ln(I_e) vs V liefert die Steigung s = 1/T_e [eV⁻¹],
        also T_e = 1/s [eV].

        Es werden nur Punkte mit I_e > 10 fA (B2910BL Rauschgrenze) verwendet,
        um log(0)-Artefakte nahe V_fl zu vermeiden.
        """
        maske = (
            (self.V >= V_fl) &
            (self.V <= V_p) &
            (I_electron > self.RAUSCHGRENZE)  # Rauschgrenze des B2910BL
        )

        if maske.sum() < 4:
            raise AnalysisError(
                f"Nur {maske.sum()} verwendbare Punkte im Übergangsbereich "
                f"[{V_fl:.2f}, {V_p:.2f}] V für den T_e-Fit. "
                "Mögliche Ursachen: V_fl ≈ V_p (schmaler Übergang), falscher "
                "Ionenfit-Bereich oder unzureichende Sonden-Bias-Abdeckung."
            )

        ln_Ie    = np.log(I_electron[maske])
        V_bereich = self.V[maske]
        poly_te  = np.polyfit(V_bereich, ln_Ie, 1)
        steigung = poly_te[0]

        if steigung <= 0.0:
            raise AnalysisError(
                f"Nicht-physikalische T_e-Steigung {steigung:.4g} V⁻¹ (muss > 0 sein). "
                "Prüfen ob der Ionenfit-Bereich im reinen Ionensättigungsplateau liegt "
                "und nicht den Übergangsbereich einschließt."
            )

        T_e = 1.0 / steigung   # Elektronentemperatur in eV
        return poly_te, float(T_e), ln_Ie, V_bereich

    def _elektronensaettigung_fitten(
        self, I_s: np.ndarray, V_p: float
    ) -> Tuple[Optional[np.ndarray], float]:
        """
        Schritt 7 – Elektronensättigungsstrom mit Scheidenexpansions-Korrektur.

        Oberhalb von V_p werden alle Elektronen gesammelt, aber die I(V)-Kurve
        steigt noch leicht an, weil die Sondeneinzugsfläche mit der Spannung zunimmt
        (Bohm-Scheidenkriterium). Ein linearer Fit an I(V) für V > V_p extrahiert
        diese Steigung. Die Rückextrapolation auf V_p liefert den 'wahren'
        Elektronensättigungsstrom ohne Scheidenexpansions-Artefakt.
        """
        maske = self.V > V_p
        if maske.sum() < 3:
            # Zu wenige Punkte für einen Fit – Wert direkt bei V_p nehmen
            warnings.warn(
                "Weniger als 3 Datenpunkte oberhalb von V_p; I_e_sat wird als I(V_p) genommen.",
                stacklevel=3,
            )
            idx_vp = int(np.argmin(np.abs(self.V - V_p)))
            return None, float(I_s[idx_vp])

        # Linearer Fit (Polynom Grad 1) im Elektronensättigungsbereich
        poly_esat = np.polyfit(self.V[maske], I_s[maske], 1)
        I_e_sat   = float(np.polyval(poly_esat, V_p))
        return poly_esat, I_e_sat
