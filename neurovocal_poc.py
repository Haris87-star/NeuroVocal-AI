"""
NeuroVocal AI - Real-Time Digital Signal Processing (DSP) Acoustic Biomarker Pipeline
Hackering 2.0 (Real-Time Voice AI Track)

Extracts clinical acoustic biomarkers from live microphone audio:
- F0 (Fundamental Frequency / Pitch)
- Jitter (Cycle-to-cycle frequency perturbation)
- HNR (Harmonics-to-Noise Ratio in dB)
- Formants F1 & F2 (Vocal tract resonances via Linear Predictive Coding - LPC)
- CSI Score (Composite Stroke & Hypoxia Index: 0.0 Healthy to 1.0 Acute Distress)
- Status Classification (HEALTHY, MILD_STRAIN, SUSPECTED_HYPOXIA, ACUTE_STROKE_ALERT, SILENCE)
"""

import math
import sys
import time
import numpy as np
from scipy.signal import lfilter

# Default Audio Parameters
SAMPLE_RATE = 16000          # 16 kHz standard telephony / speech processing rate
FRAME_SIZE = 1024            # 64 ms analysis frame
HOP_SIZE = 400               # 25 ms hop size (40 Hz update rate, sub-sampled to 25 Hz)
BUFFER_CAPACITY = 8192       # ~0.512 seconds circular audio history
SILENCE_THRESHOLD = 0.008    # RMS below this is treated as ambient silence/pause


def levinson_durbin(r, order):
    """
    Solve the Yule-Walker equations using Levinson-Durbin recursion for LPC coefficients.
    r: Autocorrelation sequence of length order + 1
    order: Prediction order
    """
    a = np.zeros(order + 1)
    a[0] = 1.0
    if len(r) <= order or r[0] <= 1e-12:
        return a

    e = r[0]
    for i in range(1, order + 1):
        gamma = 0.0
        for j in range(1, i):
            gamma += a[j] * r[i - j]
        gamma = (r[i] - gamma) / e
        
        # Stability check: reflection coefficient must be bounded
        if abs(gamma) >= 1.0:
            gamma = np.sign(gamma) * 0.99

        a_prev = a.copy()
        a[i] = gamma
        for j in range(1, i):
            a[j] = a_prev[j] - gamma * a_prev[i - j]

        e *= (1.0 - gamma * gamma)
        if e <= 1e-12:
            break

    # Standard LPC sign convention: A(z) = 1 - sum(a_k * z^-k)
    lpc_coeffs = np.zeros(order + 1)
    lpc_coeffs[0] = 1.0
    lpc_coeffs[1:] = -a[1:]
    return lpc_coeffs


class AcousticBiomarkerExtractor:
    """
    Asynchronous DSP extractor computing pitch, perturbation, spectral purity,
    and vocal tract resonances from streaming audio frames.
    """

    def __init__(self, sample_rate=SAMPLE_RATE, min_f0=70.0, max_f0=400.0, lpc_order=14):
        self.sample_rate = sample_rate
        self.min_lag = int(sample_rate / max_f0)   # ~40 samples at 16kHz (400 Hz)
        self.max_lag = int(sample_rate / min_f0)   # ~228 samples at 16kHz (70 Hz)
        self.lpc_order = lpc_order
        
        # Historical buffer for smoothed jitter estimation
        self.pitch_periods = []
        self.max_history_periods = 20

    def calculate_rms(self, frame: np.ndarray) -> float:
        """Compute Root Mean Square (RMS) energy of audio frame."""
        if len(frame) == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(frame)) + 1e-12))

    def extract_f0_and_hnr(self, frame: np.ndarray):
        """
        Estimate Fundamental Frequency (F0 in Hz) and Harmonics-to-Noise Ratio (HNR in dB)
        using Normalized Autocorrelation with parabolic peak interpolation.
        """
        # Apply Hamming window to reduce spectral leakage
        windowed = frame * np.hamming(len(frame))
        
        # Compute autocorrelation via FFT for speed
        n = len(windowed)
        n_fft = 2 ** math.ceil(math.log2(2 * n - 1))
        fft_data = np.fft.rfft(windowed, n_fft)
        autocorr = np.fft.irfft(fft_data * np.conj(fft_data), n_fft)[:n]
        
        r0 = autocorr[0]
        if r0 <= 1e-10:
            return 0.0, 0.0

        norm_autocorr = autocorr / r0
        
        # Search for highest peak in legitimate human vocal range
        search_region = norm_autocorr[self.min_lag:self.max_lag]
        if len(search_region) == 0:
            return 0.0, 0.0

        peak_idx = int(np.argmax(search_region)) + self.min_lag
        peak_val = norm_autocorr[peak_idx]

        # Parabolic interpolation for fine sub-sample resolution
        if self.min_lag < peak_idx < len(norm_autocorr) - 1:
            alpha = norm_autocorr[peak_idx - 1]
            beta = norm_autocorr[peak_idx]
            gamma = norm_autocorr[peak_idx + 1]
            denom = 2 * (2 * beta - alpha - gamma) + 1e-12
            delta = (gamma - alpha) / denom
            refined_lag = peak_idx + delta
            peak_val = max(0.0, min(1.0, beta - 0.25 * (alpha - gamma) * delta))
        else:
            refined_lag = float(peak_idx)

        # Voiced speech detection threshold
        if peak_val > 0.30 and refined_lag > 0:
            f0 = self.sample_rate / refined_lag
            # HNR calculation in dB: 10 * log10(R_peak / (R_0 - R_peak))
            bounded_peak = min(0.999, max(0.01, peak_val))
            hnr = 10.0 * math.log10(bounded_peak / (1.0 - bounded_peak + 1e-12))
            hnr = max(0.0, min(35.0, hnr))
            return float(f0), float(hnr)
        else:
            return 0.0, 0.0

    def compute_jitter(self, current_f0: float) -> float:
        """
        Compute Relative Jitter (Period Perturbation Quotient) across recent voiced frames.
        Normal: < 1.04%. Dysarthric/Tremor/Distress: > 2.5%.
        """
        if current_f0 > 0:
            period_ms = 1000.0 / current_f0
            self.pitch_periods.append(period_ms)
            if len(self.pitch_periods) > self.max_history_periods:
                self.pitch_periods.pop(0)
        else:
            # Decay history on unvoiced frames
            if len(self.pitch_periods) > 5:
                self.pitch_periods.pop(0)

        if len(self.pitch_periods) < 4:
            return 0.008  # Baseline nominal jitter

        periods = np.array(self.pitch_periods)
        mean_period = np.mean(periods)
        if mean_period <= 1e-6:
            return 0.0

        # Mean absolute difference between successive periods normalized by mean period
        diffs = np.abs(np.diff(periods))
        jitter = float(np.mean(diffs) / mean_period)
        return min(0.15, max(0.0, jitter))

    def extract_formants(self, frame: np.ndarray):
        """
        Extract Formant Frequencies (F1 and F2 in Hz) using Linear Predictive Coding (LPC).
        In dysarthria/stroke, vocal tract mobility degrades causing vowel space centralization
        (F1 & F2 collapse toward neutral tract resonance ~500 Hz & 1500 Hz).
        """
        # Pre-emphasis filter to boost high frequencies: y[n] = x[n] - 0.95 * x[n-1]
        pre_emphasized = lfilter([1, -0.95], [1], frame)
        windowed = pre_emphasized * np.hamming(len(pre_emphasized))
        
        # Compute autocorrelation sequence
        n = len(windowed)
        n_fft = 2 ** math.ceil(math.log2(2 * n - 1))
        fft_data = np.fft.rfft(windowed, n_fft)
        r = np.fft.irfft(fft_data * np.conj(fft_data), n_fft)[:self.lpc_order + 1]

        # Solve LPC coefficients
        a = levinson_durbin(r, self.lpc_order)
        if np.any(np.isnan(a)) or np.any(np.isinf(a)):
            return 500.0, 1500.0

        # Find roots of the polynomial A(z)
        try:
            roots = np.roots(a)
        except Exception:
            return 500.0, 1500.0

        # Keep roots in upper complex half-plane
        roots = [r for r in roots if np.imag(r) > 0.01 and np.abs(r) <= 1.0]

        formants = []
        for r in roots:
            # Resonance frequency
            freq = np.arctan2(np.imag(r), np.real(r)) * (self.sample_rate / (2.0 * np.pi))
            # Bandwidth of the resonance
            bandwidth = -0.5 * (self.sample_rate / np.pi) * np.log(np.abs(r))
            
            # Filter biologically plausible speech formants (bandwidth < 450 Hz, 250 Hz <= freq <= 3800 Hz)
            if 250.0 <= freq <= 3800.0 and bandwidth < 450.0:
                formants.append(freq)

        formants.sort()
        f1 = float(formants[0]) if len(formants) > 0 else 550.0
        f2 = float(formants[1]) if len(formants) > 1 else 1650.0
        return f1, f2

    def calculate_csi(self, rms: float, f0: float, jitter: float, hnr: float, f1: float, f2: float) -> tuple[float, str]:
        """
        Calculate the Composite Stroke & Hypoxia Index (CSI Score: 0.0 to 1.0) and status label.
        
        Clinical Indicators:
        - Acute Stroke / Dysarthria:
            * Formant centralization: F2 shifts towards ~1500 Hz, F1/F2 dispersion collapses.
            * Elevated jitter (> 0.025 / 2.5%) due to neuromuscular vocal fold asymmetry.
        - Hypoxia / Respiratory Failure:
            * Severely depressed HNR (< 12 dB) due to turbulent airflow and breathiness.
            * High jitter and pitch tremor.
        """
        if rms < SILENCE_THRESHOLD:
            return 0.05, "SILENCE"

        # 1. Jitter penalty: normal < 0.012, elevated > 0.025
        jitter_penalty = np.clip((jitter - 0.012) / 0.028, 0.0, 1.0)

        # 2. HNR penalty: healthy > 18 dB, critical < 10 dB
        hnr_penalty = np.clip((18.0 - hnr) / 12.0, 0.0, 1.0) if hnr > 0 else 0.5

        # 3. Formant Centralization Penalty (Vocal Tract Dysarthria Metric)
        # Healthy vowels have distinct separation: e.g., /i/ F2 ~ 2200Hz, /a/ F1 ~ 750Hz
        # Stroke patients exhibit severe centralization: F2 moves to 1450-1550 Hz
        formant_collapse = np.clip(1.0 - abs(f2 - 1500.0) / 700.0, 0.0, 1.0)

        # Weighted Composite Score
        csi = float(0.40 * jitter_penalty + 0.35 * formant_collapse + 0.25 * hnr_penalty)
        csi = round(float(np.clip(csi, 0.0, 1.0)), 3)

        # Clinical Triage Classification
        if csi >= 0.65 or (jitter >= 0.032 and formant_collapse >= 0.75):
            status = "ACUTE_STROKE_ALERT"
        elif hnr_penalty >= 0.70 and hnr < 11.0:
            status = "SUSPECTED_HYPOXIA"
        elif csi >= 0.35:
            status = "MILD_STRAIN"
        else:
            status = "HEALTHY"

        return csi, status

    def analyze_frame(self, frame: np.ndarray) -> dict:
        """Full biomarker extraction pass on an audio frame."""
        rms = self.calculate_rms(frame)
        
        if rms < SILENCE_THRESHOLD:
            return {
                "timestamp": round(time.time(), 4),
                "rms": round(rms, 4),
                "f0": 0.0,
                "jitter": 0.008,
                "hnr": 0.0,
                "f1": 500.0,
                "f2": 1500.0,
                "csi_score": 0.04,
                "status_label": "SILENCE"
            }

        f0, hnr = self.extract_f0_and_hnr(frame)
        jitter = self.compute_jitter(f0)
        f1, f2 = self.extract_formants(frame)
        csi, status = self.calculate_csi(rms, f0, jitter, hnr, f1, f2)

        return {
            "timestamp": round(time.time(), 4),
            "rms": round(rms, 4),
            "f0": round(f0, 1),
            "jitter": round(jitter, 4),
            "hnr": round(hnr, 1),
            "f1": round(f1, 1),
            "f2": round(f2, 1),
            "csi_score": csi,
            "status_label": status
        }


class SimulatedAudioGenerator:
    """
    Generates synthetic speech waveforms mimicking different patient conditions
    for offline testing and automated evaluation.
    """

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.phase = 0.0
        self.t = 0.0

    def generate_frame(self, mode="healthy", num_samples=FRAME_SIZE) -> np.ndarray:
        t_arr = np.linspace(self.t, self.t + (num_samples / self.sample_rate), num_samples, endpoint=False)
        self.t += num_samples / self.sample_rate

        if mode == "silence":
            return np.random.normal(0, 0.001, num_samples).astype(np.float32)

        if mode == "healthy":
            # Clear phonation: F0 ~ 140 Hz, F1 ~ 700 Hz, F2 ~ 1800 Hz, high HNR
            f0 = 140.0 + 3.0 * np.sin(2 * np.pi * 1.2 * t_arr)
            carrier = np.sin(2 * np.pi * f0 * t_arr)
            vocal_tract = 0.5 * np.sin(2 * np.pi * 700.0 * t_arr) + 0.3 * np.sin(2 * np.pi * 1800.0 * t_arr)
            signal = 0.15 * (carrier + vocal_tract) + np.random.normal(0, 0.003, num_samples)

        elif mode == "stroke":
            # Acute stroke / dysarthria:
            # Vocal fold paresis -> high jitter (frequency jitter jumps 30-50Hz)
            # Vowel centralization -> F1 collapses to ~500Hz, F2 collapses to ~1500Hz
            jitter_noise = np.random.normal(0, 18.0, num_samples)
            f0 = 125.0 + jitter_noise
            carrier = np.sin(2 * np.pi * f0 * t_arr)
            centralized_tract = 0.6 * np.sin(2 * np.pi * 510.0 * t_arr) + 0.5 * np.sin(2 * np.pi * 1490.0 * t_arr)
            signal = 0.18 * (carrier + centralized_tract) + np.random.normal(0, 0.008, num_samples)

        elif mode == "hypoxia":
            # Severe hypoxia / respiratory strain:
            # Low HNR (high turbulent noise / breathiness), shallow amplitude modulation
            f0 = 175.0 + 8.0 * np.sin(2 * np.pi * 4.0 * t_arr)
            carrier = np.sin(2 * np.pi * f0 * t_arr)
            turbulent_noise = np.random.normal(0, 0.045, num_samples)
            signal = 0.08 * carrier + turbulent_noise

        else:
            signal = np.random.normal(0, 0.002, num_samples)

        return signal.astype(np.float32)


def run_standalone_poc(mode="live"):
    """Terminal PoC with live visual telemetry."""
    print("=" * 70)
    print(" NeuroVocal AI - Real-Time Acoustic Biomarker Engine (PoC)")
    print(f" Mode: {mode.upper()} | Sample Rate: {SAMPLE_RATE} Hz | Window: {FRAME_SIZE} samples")
    print("=" * 70)

    extractor = AcousticBiomarkerExtractor(SAMPLE_RATE)
    
    if mode == "live":
        try:
            import sounddevice as sd
            print("[INFO] Initializing live microphone stream...")
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, blocksize=HOP_SIZE, dtype='float32')
            stream.start()
            print("[INFO] Live microphone streaming active. Speak into the mic (Press Ctrl+C to stop)...")
            
            buffer = np.zeros(FRAME_SIZE, dtype=np.float32)
            while True:
                data, _ = stream.read(HOP_SIZE)
                chunk = data[:, 0]
                buffer = np.roll(buffer, -HOP_SIZE)
                buffer[-HOP_SIZE:] = chunk
                
                metrics = extractor.analyze_frame(buffer)
                render_terminal_telemetry(metrics)
                time.sleep(0.04)  # 25 Hz update rate
        except Exception as e:
            print(f"[WARN] Live audio capture failed: {e}. Falling back to simulation mode.")
            mode = "simulate"

    if mode in ["simulate", "test"]:
        sim = SimulatedAudioGenerator(SAMPLE_RATE)
        scenarios = ["healthy", "stroke", "hypoxia"]
        for scenario in scenarios:
            print(f"\n---> Simulating Scenario: {scenario.upper()} (2.5 seconds) <---")
            for _ in range(60):
                frame = sim.generate_frame(scenario)
                metrics = extractor.analyze_frame(frame)
                render_terminal_telemetry(metrics)
                time.sleep(0.04)


def render_terminal_telemetry(m: dict):
    """Format acoustic telemetry packet as a single ANSI-updated terminal line."""
    status = m["status_label"]
    color = "\033[92m" if status == "HEALTHY" else (
        "\033[91m\033[1m" if status == "ACUTE_STROKE_ALERT" else (
            "\033[93m" if status == "SUSPECTED_HYPOXIA" else "\033[90m"
        )
    )
    reset = "\033[0m"
    
    sys.stdout.write(
        f"\r[RMS: {m['rms']:0.4f} | F0: {m['f0']:5.1f}Hz | Jitter: {m['jitter']*100:4.2f}% | "
        f"HNR: {m['hnr']:4.1f}dB | F1: {m['f1']:5.0f}Hz | F2: {m['f2']:5.0f}Hz | "
        f"CSI: {m['csi_score']:0.2f} | Status: {color}{status:<18}{reset}]"
    )
    sys.stdout.flush()


if __name__ == "__main__":
    cli_mode = "test" if "--test" in sys.argv else ("simulate" if "--simulate" in sys.argv else "live")
    try:
        run_standalone_poc(cli_mode)
    except KeyboardInterrupt:
        print("\n[INFO] Stopped NeuroVocal biomarker engine.")
