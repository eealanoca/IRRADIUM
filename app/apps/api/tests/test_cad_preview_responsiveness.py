import asyncio
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import UploadFile
from apps.api.sim_gamma_api import main as api


class CadPreviewResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_cad_conversion_keeps_the_api_event_loop_available(self):
        started, release = Event(), Event()

        def conversion(_):
            started.set()
            release.wait(1)
            return {'parts': []}

        with TemporaryDirectory() as directory, \
             patch.object(api, 'settings', SimpleNamespace(cache_root=Path(directory))), \
             patch.object(api, '_component_preview_manifest', side_effect=conversion):
            upload = UploadFile(filename='load.step', file=BytesIO(b'STEP test'))
            task = asyncio.create_task(api.preview_step(upload))
            try:
                while not started.is_set() and not task.done():
                    await asyncio.sleep(.01)
                # With synchronous CAD on the event loop, this continuation
                # cannot run until conversion completes and task is done.
                self.assertFalse(task.done(), 'CAD conversion blocked API status/progress requests')
            finally:
                release.set()
            self.assertEqual(await asyncio.wait_for(task, 2), {'parts': []})
