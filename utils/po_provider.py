"""Run a local PO token server for the lifetime of the bot."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from urllib.request import ProxyHandler, build_opener

from config import BASE_DIR

log = logging.getLogger(__name__)


class POTokenServer:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None

    @staticmethod
    def healthy() -> bool:
        try:
            opener = build_opener(ProxyHandler({}))
            with opener.open("http://127.0.0.1:4416/ping", timeout=0.5) as response:
                return json.load(response).get("version") == "2.0.2"
        except (OSError, ValueError):
            return False

    def start(self) -> bool:
        if self.healthy():
            return True  # Reuse an external server without owning its lifetime.
        node = shutil.which("node")
        script = BASE_DIR / ".local/bgutil-ytdlp-pot-provider/server/build/main.js"
        if not node or not script.is_file():
            return False
        log_dir = BASE_DIR / "logs"
        log_dir.mkdir(exist_ok=True)
        try:
            with (log_dir / "po-provider.log").open("ab", buffering=0) as output:
                self.process = subprocess.Popen(
                    [node, str(script), "--host", "127.0.0.1"],
                    cwd=script.parent.parent,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            for _ in range(40):
                if self.process.poll() is not None:
                    break
                if self.healthy():
                    log.info("PO Token HTTP provider ready on 127.0.0.1:4416")
                    return True
                time.sleep(0.1)
        except OSError as exc:
            log.warning("Cannot start PO Token provider: %s", exc)
        self.stop()
        log.warning("PO Token HTTP provider unavailable; using script fallback")
        return False

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
