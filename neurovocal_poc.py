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

# Audio Parameters
SAMPLE_RATE = 16000          # 16 kHz standard telephony / speech processing rate
FRAME_SIZE = 1024            # 64 ms analysis frame
HOP_SIZE = 400               # 25 ms hop size (sub-sampled to 25 Hz)
BUFFER_CAPACITY = 8192       # ~0.512 seconds circular audio history
SILENCE_THRESHOLD = 0.008    # RMS below this is treated as ambient silence/pause


def levinson_durbin(r, order):
    """
    Solve Yule-Walker equations using Levinson-Durbin recursion for LPC coefficients.
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
        
        if abs(gamma) >= 1.0:
            gamma = np.sign(gamma) * 0.99

        a_prev = a.copy()
        a[i] = gamma
        for j in range(1, i):
            a[j] = a_prev[j] - gamma * a_prev[i - j]

        e *= (1.0 - gamma * gamma)
        if e <= 1e-12:
            break

    lpc_coeffs = np.zeros(order + 1)
    lpc_coeffs[0] = 1.0
    lpc_coeffs[1:] = -a[1:]
    return lpc_coeffs


class AcousticBiomarkerExtractor:
    """
    DSP extractor computing pitch, perturbation, spectral purity,
    and vocal tract resonances from streaming audio frames.
    Includes Exponential Moving Average (EMA) smoothing and alert latching.
    """

    def __init__(self, sample_rate=SAMPLE_RATE, min_f0=70.0, max_f0=400.0, lpc_order=14):
        self.sample_rate = sample_rate
        self.min_lag = int(sample_rate / max_f0)   # ~40 samples at 16kHz (400 Hz)
        self.max_lag = int(sample_rate / min_f0)   # ~228 samples at 16kHz (70 Hz)
        self.lpc_order = lpc_order
        
        # Historical buffer for smoothed jitter estimation
        self.pitch_periods = []
        self.max_history_periods = 20

        # Exponential Moving Average (EMA) smoothed state (prevents glitchy fluctuations)
        self.smooth_csi = 0.05
        self.smooth_jitter = 0.008
        self.smooth_hnr = 18.0
        self.smooth_f1 = 550.0
        self.smooth_f2 = 1650.0
        self.smooth_f0 = 0.0
        
        # Alert latching (holds alert for 1.2s to prevent flickering)
        self.stroke_alert_timer = 0
        self.alert_hold_frames = 30

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
        windowed = frame * np.hamming(len(frame))
        
        n = len(windowed)
        n_fft = 2 ** math.ceil(math.log2(2 * n - 1))
        fft_data = np.fft.rfft(windowed, n_fft)
        autocorr = np.fft.irfft(fft_data * np.conj(fft_data), n_fft)[:n]
        
        r0 = autocorr[0]
        if r0 <= 1e-10:
            return 0.0, 0.0

        norm_autocorr = autocorr / r0
        
        search_region = norm_autocorr[self.min_lag:self.max_lag]
        if len(search_region) == 0:
            return 0.0, 0.0

        peak_idx = int(np.argmax(search_region)) + self.min_lag
        peak_val = norm_autocorr[peak_idx]

        # Parabolic interpolation
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

        # Voiced threshold
        if peak_val > 0.32 and refined_lag > 0:
            f0 = self.sample_rate / refined_lag
            bounded_peak = min(0.999, max(0.01, peak_val))
            hnr = 10.0 * math.log10(bounded_peak / (1.0 - bounded_peak + 1e-12))
            hnr = max(0.0, min(35.0, hnr))
            return float(f0), float(hnr)
        else:
            return 0.0, 0.0

    def compute_jitter(self, current_f0: float) -> float:
        """Compute Relative Jitter (Period Perturbation Quotient)."""
        if current_f0 > 0:
            period_ms = 1000.0 / current_f0
            self.pitch_periods.append(period_ms)
            if len(self.pitch_periods) > self.max_history_periods:
                self.pitch_periods.pop(0)
        else:
            if len(self.pitch_periods) > 4:
                self.pitch_periods.pop(0)

        if len(self.pitch_periods) < 4:
            return 0.008

        periods = np.array(self.pitch_periods)
        mean_period = np.mean(periods)
        if mean_period <= 1e-6:
            return 0.008

        diffs = np.abs(np.diff(periods))
        jitter = float(np.mean(diffs) / mean_period)
        return min(0.12, max(0.005, jitter))

    def extract_formants(self, frame: np.ndarray):
        """Extract Formant Frequencies (F1 and F2 in Hz) using LPC."""
        pre_emphasized = lfilter([1, -0.95], [1], frame)
        windowed = pre_emphasized * np.hamming(len(pre_emphasized))
        
        n = len(windowed)
        n_fft = 2 ** math.ceil(math.log2(2 * n - 1))
        fft_data = np.fft.rfft(windowed, n_fft)
        r = np.fft.irfft(fft_data * np.conj(fft_data), n_fft)[:self.lpc_order + 1]

        a = levinson_durbin(r, self.lpc_order)
        if np.any(np.isnan(a)) or np.any(np.isinf(a)):
            return 550.0, 1650.0

        try:
            roots = np.roots(a)
        except Exception:
            return 550.0, 1650.0

        roots = [r for r in roots if np.imag(r) > 0.01 and np.abs(r) <= 1.0]

        formants = []
        for r in roots:
            freq = np.arctan2(np.imag(r), np.real(r)) * (self.sample_rate / (2.0 * np.pi))
            bandwidth = -0.5 * (self.sample_rate / np.pi) * np.log(np.abs(r))
            if 250.0 <= freq <= 3600.0 and bandwidth < 450.0:
                formants.append(freq)

        formants.sort()
        f1 = float(formants[0]) if len(formants) > 0 else 550.0
        f2 = float(formants[1]) if len(formants) > 1 else 1650.0
        return f1, f2

    def calculate_csi(self, rms: float, f0: float, jitter: float, hnr: float, f1: float, f2: float) -> tuple[float, str]:
        """
        Calculate smoothed Composite Stroke & Hypoxia Index (CSI Score) with alert latching.
        """
        if rms < SILENCE_THRESHOLD:
            # Smoothly decay towards baseline
            self.smooth_csi = max(0.04, self.smooth_csi * 0.94)
            if self.stroke_alert_timer > 0:
                self.stroke_alert_timer -= 1
                return round(self.smooth_csi, 2), "ACUTE_STROKE_ALERT"
            return round(self.smooth_csi, 2), "SILENCE"

        # 1. Jitter penalty (vocal cord paresis)
        jitter_penalty = np.clip((jitter - 0.012) / 0.024, 0.0, 1.0)

        # 2. HNR penalty (airway turbulence / hypoxia)
        hnr_penalty = np.clip((18.0 - hnr) / 12.0, 0.0, 1.0) if hnr > 0 else 0.4

        # 3. Formant Centralization Penalty (Vowel space collapse toward 1500 Hz)
        formant_collapse = np.clip(1.0 - abs(f2 - 1500.0) / 600.0, 0.0, 1.0)

        raw_csi = float(0.40 * jitter_penalty + 0.40 * formant_collapse + 0.20 * hnr_penalty)
        
        # Exponential moving average for clinical stability
        alpha = 0.20
        self.smooth_csi = alpha * raw_csi + (1.0 - alpha) * self.smooth_csi
        csi = round(float(np.clip(self.smooth_csi, 0.0, 1.0)), 2)

        # Hysteresis alert latching
        if csi >= 0.58 or (jitter >= 0.028 and formant_collapse >= 0.70):
            self.stroke_alert_timer = self.alert_hold_frames
            status = "ACUTE_STROKE_ALERT"
        elif self.stroke_alert_timer > 0:
            self.stroke_alert_timer -= 1
            status = "ACUTE_STROKE_ALERT"
        elif hnr_penalty >= 0.70 and hnr < 11.0:
            status = "SUSPECTED_HYPOXIA"
        elif csi >= 0.35:
            status = "MILD_STRAIN"
        else:
            status = "HEALTHY"

        return csi, status

    def analyze_frame(self, frame: np.ndarray) -> dict:
        """Full biomarker extraction pass on an audio frame with smooth transitions."""
        rms = self.calculate_rms(frame)
        
        if rms < SILENCE_THRESHOLD:
            csi, status = self.calculate_csi(rms, 0.0, 0.008, 0.0, self.smooth_f1, self.smooth_f2)
            return {
                "timestamp": round(time.time(), 4),
                "rms": round(rms, 4),
                "f0": 0.0,
                "jitter": 0.008,
                "hnr": 0.0,
                "f1": round(self.smooth_f1, 1),
                "f2": round(self.smooth_f2, 1),
                "csi_score": csi,
                "status_label": status
            }

        raw_f0, raw_hnr = self.extract_f0_and_hnr(frame)
        raw_jitter = self.compute_jitter(raw_f0)
        raw_f1, raw_f2 = self.extract_formants(frame)

        # Smooth biomarkers to eliminate flickering
        if raw_f0 > 0:
            self.smooth_f0 = 0.30 * raw_f0 + 0.70 * self.smooth_f0 if self.smooth_f0 > 0 else raw_f0
        else:
            self.smooth_f0 = 0.0

        self.smooth_jitter = 0.25 * raw_jitter + 0.75 * self.smooth_jitter
        self.smooth_hnr = 0.25 * raw_hnr + 0.75 * self.smooth_hnr
        self.smooth_f1 = 0.20 * raw_f1 + 0.80 * self.smooth_f1
        self.smooth_f2 = 0.20 * raw_f2 + 0.80 * self.smooth_f2

        csi, status = self.calculate_csi(rms, self.smooth_f0, self.smooth_jitter, self.smooth_hnr, self.smooth_f1, self.smooth_f2)

        return {
            "timestamp": round(time.time(), 4),
            "rms": round(rms, 4),
            "f0": round(self.smooth_f0, 1),
            "jitter": round(self.smooth_jitter, 4),
            "hnr": round(self.smooth_hnr, 1),
            "f1": round(self.smooth_f1, 1),
            "f2": round(self.smooth_f2, 1),
            "csi_score": csi,
            "status_label": status
        }


class SimulatedAudioGenerator:
    """
    Continuous-phase synthetic speech generator for demonstrating clinical conditions.
    """

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.current_phase = 0.0
        self.time_offset = 0.0

    def generate_frame(self, mode="healthy", num_samples=FRAME_SIZE) -> np.ndarray:
        t_arr = np.linspace(self.time_offset, self.time_offset + (num_samples / self.sample_rate), num_samples, endpoint=False)
        self.time_offset += num_samples / self.sample_rate

        if mode == "stroke":
            # Acute Stroke / Dysarthria Simulation:
            # Continuous phase integration prevents phase cracking
            tremor = 14.0 * np.sin(2 * np.pi * 6.0 * t_arr)
            inst_f0 = 135.0 + tremor
            phase_increments = 2 * np.pi * inst_f0 / self.sample_rate
            phases = self.current_phase + np.cumsum(phase_increments)
            self.current_phase = float(phases[-1] % (2 * np.pi))
            carrier = np.sin(phases)
            
            # Dysarthric Formant Centralization: F1 ~ 515 Hz, F2 collapses to 1490 Hz
            centralized_formants = (
                0.45 * np.sin(2 * np.pi * 515.0 * t_arr) +
                0.40 * np.sin(2 * np.pi * 1490.0 * t_arr)
            )
            signal = 0.16 * (carrier + centralized_formants) + np.random.normal(0, 0.005, num_samples)
            return signal.astype(np.float32)

        else:
            # Normal Healthy Speech Simulation:
            inst_f0 = 145.0 + 2.0 * np.sin(2 * np.pi * 1.5 * t_arr)
            phase_increments = 2 * np.pi * inst_f0 / self.sample_rate
            phases = self.current_phase + np.cumsum(phase_increments)
            self.current_phase = float(phases[-1] % (2 * np.pi))
            carrier = np.sin(phases)
            
            # Normal dispersed vowel formants: F1 ~ 680 Hz, F2 ~ 1850 Hz
            healthy_formants = (
                0.35 * np.sin(2 * np.pi * 680.0 * t_arr) +
                0.30 * np.sin(2 * np.pi * 1850.0 * t_arr)
            )
            signal = 0.14 * (carrier + healthy_formants) + np.random.normal(0, 0.002, num_samples)
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
        scenarios = ["healthy", "stroke"]
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
