"""Offline extraction checks; never downloads or touches installed data."""
import hashlib,importlib.util,io,tarfile,tempfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('download',Path(__file__).with_name('install_nuclear_data.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def archive(name,body,kind=tarfile.REGTYPE):
 data=io.BytesIO()
 with tarfile.open(fileobj=data,mode='w:xz') as t:
  info=tarfile.TarInfo(name);info.type=kind;info.size=len(body);t.addfile(info,io.BytesIO(body))
 data.seek(0);return data
body=b'original fixture';profile={'files':[{'path':'photon/H.h5','sha256':hashlib.sha256(body).hexdigest()}]}
with tempfile.TemporaryDirectory() as tmp:
 dest=Path(tmp)
 assert m.consume(archive('library/photon/H.h5',body),profile,dest)==1
 assert (dest/'photon/H.h5').read_bytes()==body
 for data in [archive('../photon/H.h5',body),archive('library/photon/H.h5',b'corrupt'),archive('library/photon/H.h5',body,tarfile.SYMTYPE)]:
  try:m.consume(data,profile,None)
  except ValueError:pass
  else:raise AssertionError('Expected rejection')
print('PASS safe extraction, SHA-256 validation, traversal and symlink rejection')
