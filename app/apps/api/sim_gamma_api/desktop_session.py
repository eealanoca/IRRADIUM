"""Close local UI services when the last desktop browser window disconnects."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys


def stop_desktop_services():
    root = Path(os.environ["IRRADIUM_ROOT"])
    subprocess.Popen(
        [sys.executable, str(root / "scripts/control.py"), "close-ui", str(os.getpid())],
        cwd=root / "app", stdin=subprocess.DEVNULL, start_new_session=True,
    )


class DesktopWindows:
    def __init__(self, enabled: bool, on_close=stop_desktop_services, grace_seconds=15):
        self.enabled = enabled
        self.on_close = on_close
        self.grace_seconds = grace_seconds
        self.count = 0
        self.pending = None

    def connected(self):
        self.cancel_pending()
        self.count += 1

    def disconnected(self):
        self.count -= 1
        if self.enabled and self.count == 0:
            self.pending = asyncio.create_task(self._close_after_grace())

    async def _close_after_grace(self):
        await asyncio.sleep(self.grace_seconds)
        if self.count == 0:
            self.on_close()

    def cancel_pending(self):
        if self.pending is not None:
            self.pending.cancel()
            self.pending = None
