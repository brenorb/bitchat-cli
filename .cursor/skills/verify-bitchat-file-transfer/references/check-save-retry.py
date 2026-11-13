"""Replay a transient save error using a temporary directory and local packet."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bitchat_cli import transport as module
from bitchat_cli.crypto import Identity
from bitchat_cli.protocol import FilePacket
from bitchat_cli.state import RuntimePaths


async def check():
    with TemporaryDirectory() as temporary:
        runtime = RuntimePaths(Path(temporary))
        identity = Identity.generate()
        callbacks = []
        transport = module.BLEChatTransport(
            identity=identity,
            nickname="fixture",
            system_log=lambda *_: None,
            message_log=lambda *_: None,
            public_file_log=lambda *row: callbacks.append(row),
            file_runtime_paths=runtime,
        )
        packet = module.make_file_transfer_packet(
            identity,
            FilePacket("fixture.txt", 5, "text/plain", b"hello"),
        )
        real_save = module.save_incoming_file
        attempts = 0

        def transient_save(*args):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("temporary filesystem failure")
            return real_save(*args)

        with patch.object(module, "save_incoming_file", transient_save):
            try:
                await transport._handle_file_transfer(packet)
            except OSError as error:
                assert str(error) == "temporary filesystem failure"
            else:
                raise AssertionError("The injected write failure did not run")
            await transport._handle_file_transfer(packet)

        assert attempts == 1, attempts
        assert not callbacks, callbacks
        assert not (runtime.incoming_files_dir / "fixture.txt").exists()
        print("The retry was suppressed even though the first write did not save a file.")


asyncio.run(check())
