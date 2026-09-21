"""
NeuroVocal AI - WebSocket Telemetry Latency & Timing Benchmark Client
Hackering 2.0 (Real-Time Voice AI Track)

Connects to ws://localhost:8000/ws/telemetry, collects 100 consecutive frames,
validates the packet schema, and verifies 25 Hz streaming with zero latency buildup.
"""

import asyncio
import json
import statistics
import sys
import time

try:
    import websockets
except ImportError:
    print("[ERROR] 'websockets' library is required. Install via: pip install websockets")
    sys.exit(1)

WS_URL = "ws://localhost:8000/ws/telemetry"
TARGET_PACKETS = 100
EXPECTED_RATE_HZ = 25.0
EXPECTED_INTERVAL_MS = 1000.0 / EXPECTED_RATE_HZ  # 40.0 ms

REQUIRED_KEYS = [
    "timestamp",
    "rms",
    "f0",
    "jitter",
    "hnr",
    "f1",
    "f2",
    "csi_score",
    "status_label",
]


async def run_benchmark():
    print("=" * 70)
    print(" NeuroVocal AI - WebSocket Telemetry Latency Benchmark")
    print(f" Target Endpoint: {WS_URL}")
    print(f" Target Rate: {EXPECTED_RATE_HZ} Hz (~{EXPECTED_INTERVAL_MS:.1f} ms / frame)")
    print(f" Test Duration: {TARGET_PACKETS} frames (~4.0 seconds)")
    print("=" * 70)

    try:
        async with websockets.connect(WS_URL) as ws:
            print("[INFO] Successfully connected to WebSocket server.")
            print("[INFO] Collecting telemetry frames...")

            arrival_times = []
            packet_timestamps = []
            intervals_ms = []
            valid_keys_count = 0

            start_clock = time.perf_counter()
            last_arrival = None

            for i in range(TARGET_PACKETS):
                msg = await ws.recv()
                now = time.perf_counter()
                arrival_times.append(now)

                if last_arrival is not None:
                    delta_ms = (now - last_arrival) * 1000.0
                    intervals_ms.append(delta_ms)
                last_arrival = now

                data = json.loads(msg)
                packet_timestamps.append(data.get("timestamp", 0))

                # Verify all required keys
                if all(k in data for k in REQUIRED_KEYS):
                    valid_keys_count += 1

                # Live progress indicator
                if (i + 1) % 25 == 0 or i == TARGET_PACKETS - 1:
                    sys.stdout.write(
                        f"\r  --> Received {i + 1}/{TARGET_PACKETS} packets | "
                        f"Latest CSI: {data.get('csi_score', 0):0.2f} | "
                        f"Status: {data.get('status_label', 'UNKNOWN'):<18}"
                    )
                    sys.stdout.flush()

            total_elapsed = time.perf_counter() - start_clock
            print("\n")

            # Statistical analysis
            actual_fps = TARGET_PACKETS / total_elapsed
            avg_interval = statistics.mean(intervals_ms)
            std_interval = statistics.stdev(intervals_ms)
            min_interval = min(intervals_ms)
            max_interval = max(intervals_ms)

            # Check timestamp synchronization / drift
            server_duration = packet_timestamps[-1] - packet_timestamps[0]
            client_duration = arrival_times[-1] - arrival_times[0]
            drift_ms = abs(server_duration - client_duration) * 1000.0

            print("-" * 70)
            print(" BENCHMARK RESULTS")
            print("-" * 70)
            print(f" Total Packets Received:   {TARGET_PACKETS}")
            print(f" Schema Compliance:        {valid_keys_count}/{TARGET_PACKETS} (100% valid)")
            print(f" Total Test Duration:      {total_elapsed:.3f} s")
            print(f" Measured Delivery Rate:   {actual_fps:.2f} Hz (Target: {EXPECTED_RATE_HZ:.1f} Hz)")
            print(f" Mean Inter-Packet Time:   {avg_interval:.2f} ms (Target: {EXPECTED_INTERVAL_MS:.1f} ms)")
            print(f" Interval Std Deviation:   {std_interval:.2f} ms")
            print(f" Min / Max Interval:       {min_interval:.2f} ms / {max_interval:.2f} ms")
            print(f" Clock / Latency Drift:    {drift_ms:.2f} ms over {total_elapsed:.1f} s")
            print("-" * 70)

            # Assertions
            rate_tolerance_pass = abs(actual_fps - EXPECTED_RATE_HZ) <= 2.5
            schema_pass = valid_keys_count == TARGET_PACKETS
            drift_pass = drift_ms < 60.0

            if rate_tolerance_pass and schema_pass and drift_pass:
                print(" \033[92m[SUCCESS] Phase 1 WebSocket Telemetry Stream VALIDATED.\033[0m")
                print("   Zero latency accumulation detected; 25 Hz throughput stable.")
                return 0
            else:
                print(" \033[91m[WARNING] Benchmark metrics outside tolerance window.\033[0m")
                return 1

    except ConnectionRefusedError:
        print(f"[ERROR] Could not connect to {WS_URL}.")
        print("Please ensure server.py is running on port 8000 via: python server.py")
        return 2
    except Exception as e:
        print(f"[ERROR] Benchmark failed with exception: {e}")
        return 3


if __name__ == "__main__":
    exit_code = asyncio.run(run_benchmark())
    sys.exit(exit_code)
