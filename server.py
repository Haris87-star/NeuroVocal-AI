"""
NeuroVocal AI - Real-Time FastAPI & WebSocket Telemetry Server
Hackering 2.0 (Real-Time Voice AI Track)

Runs the DSP circular audio buffer in a dedicated background thread,
computes acoustic biomarkers (F0, Jitter, HNR, F1, F2, CSI Score),
and broadcasts JSON telemetry packets at 25 Hz (~40 ms interval)
over WebSockets with zero-latency drop guarantees.
"""

import asyncio
import json
import logging
import os
import threading
import time
from typing import Set

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from neurovocal_poc import (
    FRAME_SIZE,
    HOP_SIZE,
    SAMPLE_RATE,
    AcousticBiomarkerExtractor,
    SimulatedAudioGenerator,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("neurovocal_server")

app = FastAPI(
    title="NeuroVocal AI Telemetry Server",
    description="Dual-stream emergency triage voice AI: 25 Hz acoustic biomarker telemetry stream",
    version="1.0.0",
)

# Enable CORS for external frontends (e.g., Next.js on port 3000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TelemetryPacket(BaseModel):
    timestamp: float
    rms: float
    f0: float
    jitter: float
    hnr: float
    f1: float
    f2: float
    csi_score: float
    status_label: str
    mode: str
    fps: float


class AudioDSPManager:
    """
    Manages continuous audio capture (mic or simulation), circular ring buffering,
    and 25 Hz biomarker analysis.
    """

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.extractor = AcousticBiomarkerExtractor(sample_rate)
        self.simulator = SimulatedAudioGenerator(sample_rate)
        
        self.lock = threading.Lock()
        self.buffer = np.zeros(FRAME_SIZE * 2, dtype=np.float32)
        
        self.mode = "live"  # 'live', 'healthy', 'stroke', 'hypoxia', 'silence'
        self.running = False
        self.mic_stream = None
        self.latest_metrics = {
            "timestamp": time.time(),
            "rms": 0.0,
            "f0": 0.0,
            "jitter": 0.008,
            "hnr": 0.0,
            "f1": 500.0,
            "f2": 1500.0,
            "csi_score": 0.0,
            "status_label": "SILENCE",
            "mode": self.mode,
            "fps": 25.0,
        }

    def start_microphone(self):
        """Try initializing live microphone stream via sounddevice."""
        try:
            import sounddevice as sd
            def audio_callback(indata, frames, time_info, status):
                if status:
                    logger.warning(f"Audio stream status: {status}")
                chunk = indata[:, 0]
                with self.lock:
                    if self.mode == "live":
                        self.buffer = np.roll(self.buffer, -len(chunk))
                        self.buffer[-len(chunk):] = chunk

            self.mic_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                blocksize=HOP_SIZE,
                dtype="float32",
                callback=audio_callback,
            )
            self.mic_stream.start()
            logger.info("Live microphone input stream started successfully.")
            return True
        except Exception as e:
            logger.warning(f"Could not open hardware microphone: {e}. Defaulting mode to 'healthy' simulation.")
            self.mode = "healthy"
            return False

    def ingest_external_audio(self, pcm_bytes: bytes):
        """Allows WebSocket audio intake from browser client if needed."""
        try:
            pcm_data = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            with self.lock:
                chunk_len = len(pcm_data)
                if chunk_len > len(self.buffer):
                    pcm_data = pcm_data[-len(self.buffer):]
                    chunk_len = len(pcm_data)
                self.buffer = np.roll(self.buffer, -chunk_len)
                self.buffer[-chunk_len:] = pcm_data
        except Exception as e:
            logger.error(f"Error ingesting PCM bytes: {e}")

    def step_dsp(self):
        """Perform one 25 Hz analysis step."""
        with self.lock:
            if self.mode != "live":
                sim_frame = self.simulator.generate_frame(self.mode, num_samples=HOP_SIZE)
                self.buffer = np.roll(self.buffer, -HOP_SIZE)
                self.buffer[-HOP_SIZE:] = sim_frame

            analysis_frame = self.buffer[-FRAME_SIZE:].copy()

        metrics = self.extractor.analyze_frame(analysis_frame)
        metrics["mode"] = self.mode
        metrics["fps"] = 25.0
        self.latest_metrics = metrics
        return metrics


dsp_manager = AudioDSPManager()


class ConnectionManager:
    """Manages active WebSocket connections with zero-latency broadcast."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self.lock:
            self.active_connections.add(websocket)
        logger.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        async with self.lock:
            self.active_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast_telemetry(self, packet: dict):
        if not self.active_connections:
            return

        json_str = json.dumps(packet)
        # Drop stale frames if client buffer is blocked
        dead_clients = []
        for ws in list(self.active_connections):
            try:
                # Use send_text with timeout to prevent head-of-line blocking
                await asyncio.wait_for(ws.send_text(json_str), timeout=0.035)
            except Exception:
                dead_clients.append(ws)

        if dead_clients:
            async with self.lock:
                for ws in dead_clients:
                    self.active_connections.discard(ws)


conn_manager = ConnectionManager()
main_loop = None


def background_dsp_loop():
    """
    Dedicated background thread executing at exactly 25 Hz (40 ms period).
    """
    global main_loop
    target_period = 0.040  # 40 ms = 25 Hz
    logger.info("Starting background DSP circular buffer thread at 25 Hz...")

    next_tick = time.time()
    while dsp_manager.running:
        try:
            packet = dsp_manager.step_dsp()
            if main_loop and main_loop.is_running() and conn_manager.active_connections:
                asyncio.run_coroutine_threadsafe(conn_manager.broadcast_telemetry(packet), main_loop)
        except Exception as e:
            logger.error(f"Error in DSP loop: {e}")

        next_tick += target_period
        sleep_dur = next_tick - time.time()
        if sleep_dur > 0:
            time.sleep(sleep_dur)
        else:
            # Overrun protection: reset clock to prevent catch-up burst
            next_tick = time.time()


from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    global main_loop
    main_loop = asyncio.get_running_loop()
    
    dsp_manager.start_microphone()
    dsp_manager.running = True
    
    dsp_thread = threading.Thread(target=background_dsp_loop, daemon=True)
    dsp_thread.start()
    logger.info("NeuroVocal AI WebSocket telemetry server initialized on http://0.0.0.0:8000")
    
    yield
    
    dsp_manager.running = False
    if dsp_manager.mic_stream:
        try:
            dsp_manager.mic_stream.stop()
            dsp_manager.mic_stream.close()
        except Exception:
            pass
    logger.info("NeuroVocal AI Server stopped.")

app.router.lifespan_context = lifespan


# REST Endpoints
@app.get("/")
def get_root():
    return {
        "service": "NeuroVocal AI - Real-Time Telemetry Server",
        "track": "Hackering 2.0: Real-Time Voice AI",
        "status": "online",
        "stream_rate_hz": 25.0,
        "active_clients": len(conn_manager.active_connections),
        "telemetry_ws": "/ws/telemetry",
        "hud_client": "/client",
        "current_mode": dsp_manager.mode,
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "active_clients": len(conn_manager.active_connections),
        "mode": dsp_manager.mode,
        "sample_rate": dsp_manager.sample_rate,
    }


@app.get("/api/metrics")
def get_current_metrics():
    """Retrieve instantaneous snapshot of current acoustic biomarkers."""
    return dsp_manager.latest_metrics


class ModeRequest(BaseModel):
    mode: str  # 'live', 'healthy', 'stroke', 'hypoxia', 'silence'


@app.post("/api/simulate")
def set_simulation_mode(req: ModeRequest):
    valid_modes = ["live", "healthy", "stroke", "hypoxia", "silence"]
    if req.mode not in valid_modes:
        return JSONResponse(status_code=400, content={"error": f"Invalid mode. Choose from {valid_modes}"})

    dsp_manager.mode = req.mode
    logger.info(f"Mode switched to: {req.mode.upper()}")
    return {"message": f"DSP input mode changed to {req.mode}", "mode": req.mode}


@app.get("/client")
def serve_test_client():
    """Serve the real-time test client HUD."""
    client_path = os.path.join(os.path.dirname(__file__), "test_client.html")
    if os.path.exists(client_path):
        return FileResponse(client_path, media_type="text/html")
    return HTMLResponse("<h3>test_client.html not found. Please create it.</h3>")


# WebSocket Telemetry Stream
@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    """
    Broadcasts real-time acoustic biomarker packets at 25 Hz.
    Packet Schema:
    {
      "timestamp": float,
      "rms": float,
      "f0": float,
      "jitter": float,
      "hnr": float,
      "f1": float,
      "f2": float,
      "csi_score": float,
      "status_label": str,
      "mode": str,
      "fps": 25.0
    }
    """
    await conn_manager.connect(websocket)
    try:
        while True:
            # Keep connection open; allow client to send mode change requests or pings
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if "set_mode" in msg:
                    dsp_manager.mode = msg["set_mode"]
                    logger.info(f"Client requested mode change to {dsp_manager.mode}")
            except Exception:
                pass
    except WebSocketDisconnect:
        await conn_manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket client error: {e}")
        await conn_manager.disconnect(websocket)


@app.websocket("/ws/audio_in")
async def websocket_audio_in(websocket: WebSocket):
    """Allows browser to stream raw 16kHz 16-bit PCM mic bytes directly to server."""
    await websocket.accept()
    try:
        while True:
            audio_bytes = await websocket.receive_bytes()
            dsp_manager.ingest_external_audio(audio_bytes)
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
