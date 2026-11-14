"""Download the official OpenMC archive, streaming only the verified gamma profile.

No archive is retained. Every selected HDF5 file is checked against a pinned
SHA-256 before publication. Existing external libraries are never touched.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import tarfile
import time
import urllib.request
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]

def validate(root,profile):
    if not (root/'cross_sections.xml').is_file():return False
    try:
        for item in profile['files']:
            path=root/item['path']
            if path.is_symlink() or not path.is_file():return False
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(1024**2),b''):digest.update(block)
            if digest.hexdigest()!=item['sha256']:return False
        tree=ET.parse(root/'cross_sections.xml').getroot()
        actual={(n.get('type'),n.get('materials'),n.get('path')) for n in tree.findall('library')}
        expected={(n['type'],n['materials'],n['path']) for n in profile['files']}
        return actual==expected
    except (OSError,ET.ParseError):return False

def consume(stream,profile,destination=None):
    expected={item['path']:item for item in profile['files']};seen=set();total=0;last=time.monotonic()
    with tarfile.open(fileobj=stream,mode='r|xz') as archive:
        for member in archive:
            name=PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts:raise ValueError('Ruta insegura en la descarga')
            key='/'.join(name.parts[-2:])
            if key not in expected:continue
            if key in seen or not member.isfile():raise ValueError('Entrada inválida o duplicada: '+key)
            digest=hashlib.sha256();output=None;temporary=None
            if destination is not None:
                path=destination/key;path.parent.mkdir(parents=True,exist_ok=True)
                temporary=path.with_suffix('.part');output=temporary.open('wb')
            try:
                source=archive.extractfile(member)
                for block in iter(lambda:source.read(1024**2),b''):
                    digest.update(block);total+=len(block)
                    if output:output.write(block)
                if digest.hexdigest()!=expected[key]['sha256']:raise ValueError('SHA-256 incorrecto: '+key)
            finally:
                if output:output.close()
            if temporary:temporary.replace(path)
            seen.add(key)
            if time.monotonic()-last>10:
                print(f'Datos verificados: {len(seen)}/{len(expected)} · {total/1024**2:.0f} MiB',flush=True);last=time.monotonic()
    if seen!=expected.keys():raise ValueError('La descarga no contiene todos los datos requeridos: '+str(sorted(expected.keys()-seen)))
    return len(seen)

def install(root=ROOT,verify_source=False):
    profile=json.loads((root/'scripts/nuclear_data_profile.json').read_text())
    target=root/'nuclear-data';cache=root/'.local';cache.mkdir(exist_ok=True)
    print('Verificando los datos nucleares locales...',flush=True)
    if not verify_source and validate(target,profile):
        print('Datos nucleares locales verificados; no se descargan de nuevo.',flush=True)
        return
    with (cache/'nuclear-download.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if not verify_source and validate(target,profile):
            print('Datos nucleares locales verificados; no se descargan de nuevo.');return
        if not verify_source and target.exists():
            raise ValueError('La biblioteca local existe pero no coincide con el perfil. No se sobrescribió; conserva o renombra nuclear-data/ antes de reparar.')
        staging=cache/'nuclear-download';staging.mkdir(exist_ok=True)
        if not verify_source and shutil.disk_usage(root).free<profile['selected_bytes']+300*1024**2:
            raise ValueError('Espacio insuficiente: se necesitan al menos 2,8 GB libres para los datos nucleares.')
        print('Descargando desde '+profile['source_url'],flush=True)
        for attempt in range(5):
            try:
                with urllib.request.urlopen(profile['source_url'],timeout=300) as response:
                    if response.status!=200:raise ValueError('Respuesta inesperada del proveedor')
                    count=consume(response,profile,None if verify_source else staging)
                break
            except (OSError,ValueError,tarfile.TarError,EOFError) as error:
                if attempt==4:raise
                print(f'Descarga interrumpida: {error}. Reintentando...',flush=True);time.sleep(2*(attempt+1))
        if not verify_source:
            index=ET.Element('cross_sections')
            for item in profile['files']:ET.SubElement(index,'library',{k:item[k] for k in ('materials','type','path')})
            ET.indent(index);ET.ElementTree(index).write(staging/'cross_sections.xml',encoding='utf-8',xml_declaration=True)
            (staging/'manifest.json').write_text(json.dumps(profile,indent=2)+'\n')
            if not validate(staging,profile):raise ValueError('Falló la validación final de los datos')
            staging.rename(target)
        (cache/'nuclear-download-verification.json').write_text(json.dumps({'source':profile['source_url'],'files_verified':count,'all_sha256_match':True,'mode':'verify-source' if verify_source else 'install'},indent=2)+'\n')
        print(f'Biblioteca oficial verificada: {count} archivos.',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--verify-source',action='store_true')
    args=parser.parse_args();install(verify_source=args.verify_source)
