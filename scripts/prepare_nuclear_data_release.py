"""Audit or COPY a compatible gamma-data subset. Never prune the source library.

OpenMC 0.15.3 still loads neutron nuclide data to initialise photon materials.
Retain exactly the natural-isotope expansion of every supported element, plus
the complete photon files (including relaxation and bremsstrahlung).
"""
from pathlib import Path
import argparse
from collections import Counter
import hashlib
import json
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET

PROFILE = 'gamma-natural-materials-openmc-0.15.3-v1'

def sha256(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()

def entries(source):
    source=Path(source).resolve(strict=True)
    root=ET.parse(source).getroot()
    directory=Path(root.findtext('directory') or '.')
    base=directory if directory.is_absolute() else source.parent/directory
    result=[]
    for node in root.findall('library'):
        path=Path(node.attrib['path'])
        path=(path if path.is_absolute() else base/path).resolve(strict=True)
        if not path.is_file():raise ValueError('No es un archivo de datos: '+str(path))
        result.append({'type':node.attrib['type'],'materials':node.attrib['materials'], 'source':path})
    return source,result

def select(source):
    import openmc
    from openmc.data import isotopes as natural_isotopes
    if openmc.__version__ != '0.15.3':
        raise ValueError('Este perfil requiere validar de nuevo la dependencia de datos con OpenMC '+openmc.__version__)
    source,libs=entries(source)
    photons=[lib for lib in libs if lib['type']=='photon']
    if not photons:raise ValueError('La biblioteca no contiene datos de fotones')
    expansions={};unsupported=[];unavailable={};required=set()
    for element in sorted({name for lib in photons for name in lib['materials'].split()}):
        if not list(natural_isotopes(element)):
            unsupported.append(element);continue
        try:
            expanded=openmc.Element(element).expand(1.,'ao',cross_sections=str(source))
        except ValueError as error:
            # Keep the same supported elements as the full source library;
            # do not invent isotope substitutions for already missing data.
            unavailable[element]=str(error);continue
        expansions[element]=[list(item) for item in expanded]
        required.update(item[0] for item in expanded)
    compatible=[lib for lib in libs if lib['type']=='neutron' and required.intersection(lib['materials'].split())]
    if not required:
        raise ValueError('Solo fotones no es compatible con OpenMC 0.15.3: faltan los núclidos de inicialización de materiales')
    found={name for lib in compatible for name in lib['materials'].split()}
    if required-found:raise ValueError('Faltan núclidos de compatibilidad: '+', '.join(sorted(required-found)))
    selected=photons+compatible
    selected_ids={id(lib) for lib in selected}
    omitted=[lib for lib in libs if id(lib) not in selected_ids]
    def totals(items):
        unique={lib['source'] for lib in items}
        return {'entries':dict(Counter(lib['type'] for lib in items)),
                'files':len(unique),'bytes':sum(p.stat().st_size for p in unique)}
    report={'profile':PROFILE,'openmc_version':openmc.__version__,'source_xml':str(source),
            'source_xml_sha256':sha256(source),
            'transport_particles':['photon'],'photon_only_compatible':False,
            'reason':'OpenMC 0.15.3 requiere núclidos para inicializar materiales, incluso con fuentes de fotones.',
            'original':totals(libs),'selected':totals(selected),'omitted':totals(omitted),
            'natural_element_expansions':expansions,'elements_without_natural_composition':unsupported,
            'elements_already_unavailable_in_source':unavailable,
            'files':[{'type':lib['type'],'materials':lib['materials'],'source':str(lib['source'])} for lib in selected],
            'source_modified':False}
    return selected,report

def write_index(libs,destination,copy_files=False):
    destination=Path(destination)
    root=ET.Element('cross_sections')
    assigned={}
    for lib in libs:
        path=lib['source']
        if copy_files:
            relative=Path(lib['type'])/path.name
            if relative in assigned and assigned[relative]!=path:
                raise ValueError('Colisión entre archivos: '+str(relative))
            assigned[relative]=path
            target=destination.parent/relative;target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():shutil.copy2(path,target)
            path=relative
        ET.SubElement(root,'library',{'materials':lib['materials'],'type':lib['type'],'path':str(path)})
    ET.indent(root)
    ET.ElementTree(root).write(destination,encoding='utf-8',xml_declaration=True)

def bundle(source,destination):
    source=Path(source).resolve(strict=True);destination=Path(destination).resolve()
    if destination.exists():raise ValueError('El destino debe ser nuevo; nunca se recortan bibliotecas existentes')
    if destination.is_relative_to(source.parent):raise ValueError('El destino no puede estar dentro de la biblioteca original')
    libs,report=select(source)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.gamma-data-',dir=destination.parent) as temporary:
        staging=Path(temporary)/'nuclear-data';staging.mkdir()
        write_index(libs,staging/'cross_sections.xml',copy_files=True)
        # Keep any provider notices without modifying the source directory.
        for p in source.parent.iterdir():
            if p.is_file() and p.name.lower().startswith(('license','copying','notice','readme')):
                shutil.copy2(p,staging/p.name)
        portable={**report,'source_xml':source.name,
                  'files':[{'type':lib['type'],'materials':lib['materials'],
                            'path':str(Path(lib['type'])/lib['source'].name),
                            'sha256':sha256(staging/lib['type']/lib['source'].name)}
                           for lib in libs]}
        (staging/'manifest.json').write_text(json.dumps(portable,indent=2))
        staging.rename(destination)
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',default=os.environ.get('OPENMC_CROSS_SECTIONS'))
    parser.add_argument('--report',type=Path)
    parser.add_argument('--output',type=Path,help='COPIA a una carpeta nueva para distribución; sin esta opción solo audita')
    args=parser.parse_args()
    if not args.source:parser.error('Indica --source cross_sections.xml')
    report=bundle(args.source,args.output) if args.output else select(args.source)[1]
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('files','natural_element_expansions')},indent=2))

if __name__=='__main__':main()
