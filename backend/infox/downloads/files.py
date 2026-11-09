"""Windows Known Folder lookup and atomic, non-overwriting local writes."""
import ctypes
import asyncio
import hashlib
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import UUID, uuid4


def downloads_folder() -> Path:
    # FOLDERID_Downloads; respect redirected folders, never guess ~/Downloads.
    if os.name != 'nt':
        raise OSError('downloads_folder_unavailable')
    class GUID(ctypes.Structure):
        _fields_ = [('data', ctypes.c_ubyte * 16)]
    guid = GUID((ctypes.c_ubyte * 16).from_buffer_copy(UUID('374de290-123f-4565-9164-39c4925e467b').bytes_le))
    value = ctypes.c_void_p()
    shell = ctypes.WinDLL('shell32')
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole = ctypes.WinDLL('ole32')
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    try:
        if shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(value)) != 0:
            raise OSError('downloads_folder_unavailable')
        folder = Path(ctypes.wstring_at(value))
        if not folder.is_absolute() or not folder.is_dir():
            raise OSError('downloads_folder_unavailable')
        return folder
    finally:
        if value.value:
            ole.CoTaskMemFree(value)


class FileWriter:
    def __init__(self, folder: Path, filename: str):
        if Path(filename).name != filename or any(c in filename for c in '/\\:'):
            raise ValueError('invalid_filename')
        self.folder = Path(folder)
        self.filename = filename
        self.temp = self.folder / f'.infox-{uuid4().hex}.part'
        self.handle = None
        self.size = 0
        self.digest = hashlib.sha256()

    def __enter__(self):
        self.handle = self.temp.open('xb')
        return self

    def write(self, data: bytes):
        self.handle.write(data)
        self.size += len(data)
        self.digest.update(data)

    def finish(self):
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        base = Path(self.filename)
        index = 0
        while True:
            name = base.name if index == 0 else f'{base.stem} ({index}){base.suffix}'
            destination = self.folder / name
            try:
                if os.name == 'nt':
                    # Windows rename fails atomically if destination exists.
                    os.rename(self.temp, destination)
                else:
                    os.link(self.temp, destination)
                    self.temp.unlink()
                return {'path': str(destination), 'size': self.size, 'sha256': self.digest.hexdigest()}
            except FileExistsError:
                index += 1

    def __exit__(self, *args):
        if self.handle:
            self.handle.close()
        self.temp.unlink(missing_ok=True)


def write_text(folder: Path, filename: str, text: str) -> dict:
    with FileWriter(folder, filename) as writer:
        writer.write(text.encode('utf-8'))
        return writer.finish()


async def file_io(function, *args):
    """Do not close a writer while a cancelled thread is still using it."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise


@asynccontextmanager
async def async_writer(folder, filename):
    writer = FileWriter(folder, filename)
    try:
        await file_io(writer.__enter__)
        yield writer
    finally:
        await file_io(writer.__exit__, None, None, None)


def intact(item) -> bool:
    if item.state != 'completed' or not item.path:
        return False
    try:
        path = Path(item.path)
        if path.stat().st_size != item.size:
            return False
        with path.open('rb') as handle:
            return hashlib.file_digest(handle, 'sha256').hexdigest() == item.sha256
    except OSError:
        return False
