"""Share identical immutable STL inputs without coupling later geometry writes."""
import errno
import hashlib
import os
from pathlib import Path
from uuid import uuid4


def detach_surface_before_write(path: Path) -> None:
    """An exporter must create its own inode rather than modify shared inputs."""
    if path.exists() and path.stat().st_nlink > 1:
        path.unlink()


def share_surface_files(paths, cache: Path) -> int:
    """Keep every input path and byte; share storage by SHA-256 within a case."""
    cache.mkdir(parents=True, exist_ok=True)
    released = 0
    for path in paths:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"La superficie debe ser un archivo regular: {path}")
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        canonical = cache / (digest + '.stl')
        temporary = path.with_name(path.name + '.' + uuid4().hex + '.shared')
        try:
            try:
                os.link(path, canonical)
            except FileExistsError:
                with canonical.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                        raise ValueError('La copia compartida de la geometría no coincide con su hash')
            if os.path.samefile(path, canonical):
                continue
            before = path.stat()
            os.link(canonical, temporary)
            temporary.replace(path)
            if before.st_nlink == 1:
                released += before.st_blocks * 512
        except OSError as error:
            if error.errno not in {errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP}:
                raise
        finally:
            temporary.unlink(missing_ok=True)
    return released
