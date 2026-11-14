"""Project-local housekeeping. Audit by default; --apply requires stopped services.

Preserves cases, nuclear data, runtimes' libraries and unknown user temporaries.
Only immutable STEP/STL preview caches are deduplicated with hard links.
"""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]

def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def allocated(root):
    seen=set();total=0
    for directory,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
        for name in files:
            path=Path(directory)/name
            if path.is_symlink():continue
            st=path.stat();key=(st.st_dev,st.st_ino)
            if key not in seen:seen.add(key);total+=st.st_blocks*512
    return total

def protected_files(root):
    files={p for p in (root/'workspace/Configuracion').rglob('*') if p.is_file() and not p.is_symlink()};texts=[]
    for category in (root/'workspace/irradiators').glob('*'):
        if category.is_symlink() or not category.is_dir():continue
        for case in (category/'Casos').glob('*'):
            files.update(p for p in [case/'manifest.json',*(case/'input').glob('*'),*(case/'results').rglob('*')] if p.is_file() and not p.is_symlink())
            for study in (case/'results/studies').glob('*.json'):
                for position in json.loads(study.read_text()).get('positions',[]):
                    summary=case/position['summary'];files.add(summary)
                    if summary.with_name('dose_distribution.vti').is_file():files.add(summary.with_name('dose_distribution.vti'))
            for path in case.rglob('*'):
                if path.is_file() and not path.is_symlink() and path.suffix in {'.json','.xml','.inp'} and path.stat().st_size<2*1024**2:
                    text=path.read_text(errors='replace')
                    if '.local/validation' in text:texts.append(text)
        files.update(p for p in (category/'Configuracion').rglob('*') if p.is_file() and not p.is_symlink())
    return {str(p.relative_to(root)):sha256(p) for p in files},texts

def cleanup_candidates(root,texts):
    result=[]
    prefixes=('irradium-results-ui-','irradium-preparation-','air-calibration-preview','node-compile-cache','cad-check-')
    for p in (root/'.local/tmp').glob('*'):
        if not p.is_symlink() and (p.name.startswith(prefixes) or (p.is_file() and re.fullmatch(r'fort[A-Za-z0-9]+',p.name))):
            result.append(p)
    for name in ('phits-angular-pilots','phits-transport-pilots','phits-complex-conformal'):
        for p in (root/'.local/validation'/name).rglob('*'):
            if p.is_file() and not p.is_symlink() and p.suffix.lower() in {'.stl','.bdf','.h5m','.msh','.vtk','.vti','.h5'}:
                if not any(str(p) in text or str(p.relative_to(root)) in text for text in texts):result.append(p)
    # Upstream DAGMC test executables and fixtures, not application tests.
    p=root/'runtime/main/tests'
    if p.is_dir() and not p.is_symlink():result.append(p)
    return result

def preview_groups(root):
    sizes=defaultdict(list)
    for category in (root/'workspace/irradiators').glob('*'):
        if category.is_symlink() or not category.is_dir():continue
        for prefix in ('Temporales/step_previews','cache/monte-carlo-step-components','cache/phits-step-components'):
            for folder in (category/prefix).glob('*-components'):
                if not re.fullmatch(r'[0-9a-f]{20}-components',folder.name) or folder.is_symlink():continue
                for p in folder.iterdir():
                    if p.is_file() and not p.is_symlink() and p.suffix in {'.step','.stl'}:
                        st=p.stat()
                        if st.st_size>=65536:sizes[(str(category),st.st_dev,st.st_size,st.st_mode,st.st_uid,st.st_gid)].append(p)
    groups=[]
    for candidates in sizes.values():
        if len({p.stat().st_ino for p in candidates})<2:continue
        hashes=defaultdict(list)
        for p in candidates:hashes[sha256(p)].append(p)
        groups.extend(paths for paths in hashes.values() if len({p.stat().st_ino for p in paths})>1)
    return groups

def deduplicate(groups, root):
    linked=[]
    for group in groups:
        assert all(p.resolve().is_relative_to(root.resolve()) for p in group)
        canonical=max(group,key=lambda p:p.stat().st_nlink)
        expected=sha256(canonical)
        for duplicate in group:
            if duplicate.stat().st_ino==canonical.stat().st_ino:continue
            assert sha256(duplicate)==expected
            temporary=duplicate.with_name('.dedup-'+uuid4().hex)
            try:
                os.link(canonical,temporary);os.replace(temporary,duplicate)
            finally:temporary.unlink(missing_ok=True)
            linked.append(str(duplicate))
    return linked

def compress_log(path):
    stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
    destination=path.with_name(path.name+'.'+stamp+'.gz')
    if destination.exists():raise ValueError('El archivo comprimido ya existe')
    expected=sha256(path)
    with path.open('rb') as src,gzip.open(destination,'wb',compresslevel=6) as dst:shutil.copyfileobj(src,dst)
    actual=hashlib.sha256()
    with gzip.open(destination,'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):actual.update(block)
    assert actual.hexdigest()==expected
    path.write_bytes(b'')
    return {'path':str(path),'archive':str(destination),'original_sha256':expected}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    if args.apply:
        for service in ('api','web'):
            if (ROOT/'.local/state'/f'{service}.json').exists():
                raise RuntimeError('Detén IRRADIUM antes de aplicar el mantenimiento')
        for status in (ROOT/'workspace/irradiators').glob('*/Casos/*/monte_carlo/status.json'):
            if json.loads(status.read_text()).get('state') in ('running','queued','pausing','cancelling'):
                raise RuntimeError('Hay una simulación activa: '+str(status))
    protected,texts=protected_files(ROOT)
    candidates=cleanup_candidates(ROOT,texts);groups=preview_groups(ROOT)
    logs=[p for p in (ROOT/'workspace/Registros').glob('*.log') if not p.is_symlink() and p.stat().st_size>8*1024**2]
    report={'applied':args.apply,'root':str(ROOT),'allocated_bytes_before':allocated(ROOT),
            'protected_files':len(protected),'cleanup_candidates':[str(p.relative_to(ROOT)) for p in candidates],
            'duplicate_groups':len(groups),'logs_to_compress':[str(p.relative_to(ROOT)) for p in logs]}
    if args.apply:
        for p in candidates:
            assert p.resolve().is_relative_to(ROOT)
            if p.is_dir():shutil.rmtree(p)
            else:p.unlink()
        report['deduplicated_files']=deduplicate(groups,ROOT)
        assert all(p.resolve().is_relative_to(ROOT) for p in logs)
        report['compressed_logs']=[compress_log(p) for p in logs]
        altered=[name for name,value in protected.items() if not (ROOT/name).is_file() or sha256(ROOT/name)!=value]
        assert not altered,altered
        report['all_protected_files_unchanged']=True
        report['allocated_bytes_after']=allocated(ROOT)
        report['freed_bytes']=report['allocated_bytes_before']-report['allocated_bytes_after']
    if args.report:args.report.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('cleanup_candidates','deduplicated_files')},indent=2))

if __name__=='__main__':main()
