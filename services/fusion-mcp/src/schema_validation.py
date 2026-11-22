"""Bound schema CPU work without letting Python regex block the ASGI loop."""
import asyncio
import json
import os
from pathlib import Path
import sys


SCHEMA_TIMEOUT_SECONDS = 2


class SchemaBusy(Exception):
    pass


class SchemaValidator:
    def __init__(self):
        self._slots = asyncio.Semaphore(2)

    async def validate(self, schemas, calls=()):
        if not schemas:
            return True
        try:
            await asyncio.wait_for(self._slots.acquire(), .05)
        except TimeoutError:
            raise SchemaBusy from None
        process = None
        try:
            payload = json.dumps({"schemas": schemas, "calls": calls}, allow_nan=False).encode()
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", str(Path(__file__).with_name("schema_validation_worker.py")),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            )
            try:
                await asyncio.wait_for(process.communicate(payload), SCHEMA_TIMEOUT_SECONDS)
                return process.returncode == 0
            except (TimeoutError, BrokenPipeError, ConnectionResetError):
                return False
        finally:
            # Cancellation must also reap the worker before releasing admission.
            cancelled = False
            if process is not None:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                cleanup = asyncio.create_task(process.wait())
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled = True
                await cleanup
            self._slots.release()
            if cancelled:
                raise asyncio.CancelledError
