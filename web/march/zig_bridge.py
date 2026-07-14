"""Zig signal runner bridge.

Spawns the compiled Zig binary as a subprocess and communicates
over stdin/stdout pipes using a simple line-based protocol.
"""

import subprocess
import logging
from pathlib import Path

logger = logging.getLogger("march")


class ZigBridge:
    """Manages the Zig signal_runner subprocess."""

    def __init__(self, binary_path: str):
        self.binary_path = binary_path
        self.process: subprocess.Popen | None = None

    def start(self, strategy: str, config: dict | None = None) -> None:
        """Spawn the Zig process and send STRATEGY + CONFIG commands."""
        binary = Path(self.binary_path)
        if not binary.exists():
            raise FileNotFoundError(
                f"Zig binary not found at {binary}. "
                f"Run 'zig build' in march/zig/ first."
            )

        self.process = subprocess.Popen(
            [str(binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
        )

        # Send STRATEGY command
        response = self._send(f"STRATEGY {strategy}")
        if not response.startswith("OK"):
            raise RuntimeError(f"Zig strategy selection failed: {response}")
        logger.info(f"Zig process started: {response}")

        # Send CONFIG if provided
        if config:
            config_str = " ".join(f"{k}={v}" for k, v in config.items())
            response = self._send(f"CONFIG {config_str}")
            logger.info(f"Zig config set: {response}")

    def send_bar(
        self,
        timestamp: str,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: int,
    ) -> str:
        """Send a BAR line and return the signal (LONG/SHORT/FLAT/CLOSE)."""
        bar_line = (
            f"BAR {timestamp},{open_:.2f},{high:.2f},"
            f"{low:.2f},{close:.2f},{volume}"
        )
        return self._send(bar_line)

    def stop(self) -> None:
        """Send QUIT and wait for the process to exit."""
        if self.process and self.process.poll() is None:
            try:
                self._send("QUIT")
            except Exception:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            logger.info("Zig process stopped")
        self.process = None

    def _send(self, line: str) -> str:
        """Write a line to stdin and read the response from stdout."""
        if not self.process or self.process.poll() is not None:
            raise RuntimeError("Zig process is not running")

        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

        response = self.process.stdout.readline().strip()
        return response

    def __del__(self):
        self.stop()
