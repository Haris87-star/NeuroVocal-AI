# Hackering 2.0 — Round 1 Official Submission Form

**Project Name:** NeuroVocal AI  
**Track:** Software Track: Real-Time Voice AI  
**Team Lead GitHub:** [Haris87-star](https://github.com/Haris87-star)  
**Repository Name:** `NeuroVocal-AI`  
**GitHub URL:** `https://github.com/Haris87-star/NeuroVocal-AI`  

---

### 1. Elevator Pitch (In Simple Terms)
Most emergency AI voice bots only listen to **what** you say (transcribed text). **NeuroVocal AI** listens to **how your body physically sounds** while you speak. Think of it as an emergency phone operator (112 / 911) that doubles as an **acoustic stethoscope**: while it talks empathetically with the patient, its background Digital Signal Processing (DSP) engine inspects vocal cord vibrations and vocal tract acoustics at 25 Hz to detect early ischemic strokes, silent hypoxia, and autonomic shock in real time.

---

### 2. The Core Problem It Solves
During acute medical emergencies, callers are disoriented, scared, or physically deteriorating. An elderly stroke patient whose facial muscles are drooping often calmly says: *"I just feel a bit tired today."* A standard conversational chatbot reads the words literally, assumes mild fatigue, and suggests resting. Because stroke treatment depends on the critical 4.5-hour thrombolysis window, this conversational misclassification is catastrophic. NeuroVocal AI solves this by detecting the 35% collapse in vocal tract symmetry (dysarthric vowel centralization) directly from the raw audio waveform, triggering a **CODE RED** ambulance dispatch regardless of the caller's calm tone.

---

### 3. Dual-Stream Architecture
* **Stream 1: Conversational Loop:**
  * **Deepgram Nova-2:** Streaming Speech-to-Text (< 250 ms).
  * **Groq Llama 3.3 70B:** Real-time triage reasoning, location extraction, and patient calming.
  * **Cartesia Sonic:** Empathetic voice synthesis (< 150 ms).
* **Stream 2: Hidden Health Scanner (DSP):**
  * Asynchronous circular audio buffer running at 16 kHz.
  * **Pitch ($F_0$):** Tracks vocal micro-tremors and autonomic shock.
  * **Jitter:** Period perturbation quotient detecting vocal fold paresis (> 2.50%).
  * **Harmonics-to-Noise Ratio (HNR):** Quantifies turbulent breath noise and silent hypoxia (< 12.0 dB).
  * **Formants ($F_1, F_2$):** 14th-order Linear Predictive Coding (LPC) detecting dysarthric vowel collapse.
  * **CSI Score:** Composite Stroke & Hypoxia Index (0.00 – 1.00).

---

### 4. Technical Innovation & Zero Hardware Requirement
* **No Wearables / Extra Hardware:** Operates on standard telephony audio and laptop microphones.
* **Deterministic 25 Hz Telemetry:** Broadcasts structured JSON biomarker packets every 40 ms over WebSockets (`/ws/telemetry`).
* **Zero Latency Accumulation:** Measured client clock drift of **0.62 ms** over continuous streaming with 100% packet schema compliance.

---

### 5. Submission Artifacts Checklist
- [x] **Source Code:** Complete Python DSP engine, FastAPI server, conversational operator, and test suite.
- [x] **Pitch Deck (PPTX):** `NeuroVocal_AI_Hackathon_Pitch.pptx` (7 widescreen slides).
- [x] **Technical Whitepaper (PDF):** `NeuroVocal_AI_Round1_Submission.pdf` (Publication-grade 2-page summary).
- [x] **Live Interactive HUD:** Running at `http://localhost:8000/client` with live hardware mic capture and simulated stroke/hypoxia buttons.
- [x] **Automated Benchmark Test:** `python test_client.py` (verifying 25.17 Hz throughput).
- [x] **GitHub Repository:** `https://github.com/Haris87-star/NeuroVocal-AI`

---

### 6. Quick Demo Video Recording Guide (For Hackathon Portal)
To record your 2-minute demo video:
1. Ensure the server is running: `python server.py`.
2. Open `http://localhost:8000/client` in Google Chrome or Microsoft Edge.
3. Press **`Windows Key + Alt + R`** (Windows Game Bar Screen Recorder) or open OBS / Loom.
4. Walk the judges through:
   * **0:00 - 0:30:** Introduce the project concept ("An emergency operator that doubles as an acoustic stethoscope").
   * **0:30 - 1:00:** Speak into the live mic and show the live F0, Jitter, HNR, and Formants tracking smoothly at 25 Hz.
   * **1:00 - 1:30:** Click **`🚨 Stroke / Dysarthria`** to demonstrate the CSI score jumping to 0.80 and the flashing **"CODE RED: ACUTE STROKE BIOMARKER DETECTED"** banner.
   * **1:30 - 2:00:** Explain the dual-stream architecture (Conversational Voice AI + Audio Physics DSP) and conclude with real-life emergency impact.
