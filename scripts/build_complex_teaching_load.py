"""Original 54-body, five-material medical-fluid kit for the public tutorial."""
from pathlib import Path
import json
from cadquery import Assembly,Workplane,Solid
ROOT=Path(__file__).resolve().parents[1];out=ROOT/'app/examples/commercial_svst';a=Assembly(name='medical_fluid_kit')
index=0
def add(shape,tag,label):
 global index
 index+=1;a.add(shape,name=f'{tag}_{label}_{index:03d}')
for tier,bottom in enumerate((35,485)):
 for x in (-150,0,150):
  for y in (-100,100):
   shell=Workplane('XY').circle(43).extrude(235).cut(Workplane('XY').workplane(offset=5).circle(39).extrude(231))
   shell=shell.edges('|Z').fillet(1) if False else shell
   add(shell.translate((x,y,bottom)),'pp','bottle_shell')
   add(Workplane('XY').circle(38).extrude(220).translate((x,y,bottom+7)),'water','contents')
   add(Workplane('XY').circle(44).circle(27).extrude(15).translate((x,y,bottom+237)),'pc','connector_cap')
 add(Workplane('XY').box(440,440,10,centered=(True,True,False)).translate((0,0,bottom-20)),'pp','tray')
 for x in (-100,100):
  add(Solid.makeTorus(32,6).translate((x,180,bottom+290)),'silicone','tubing_loop')
 for x in (-210,210):
  for y in (-160,160):
   clip=Workplane('XY').box(20,16,60).cut(Workplane('XY').box(14,20,45).translate((0,0,5)))
   add(clip.translate((x,y,bottom+325)),'steel','clamp')
for x in (-230,230):
 for y in (-230,230):add(Workplane('XY').box(20,20,900,centered=(True,True,False)).translate((x,y,0)),'pp','post')
a.export(str(out/'medical_kit.step'))
materials=json.loads((out/'config/materials.json').read_text())
new=[{'name':'pp','density':.90,'elements':{'C':3,'H':6},'fraction_type':'atomic'}, {'name':'pc','density':1.20,'elements':{'C':16,'H':14,'O':3},'fraction_type':'atomic'}, {'name':'silicone','density':1.10,'elements':{'Si':1,'O':1,'C':2,'H':6},'fraction_type':'atomic'}, {'name':'steel','density':7.90,'elements':{'Fe':.70,'Cr':.19,'Ni':.10,'Mn':.01},'fraction_type':'weight'}]
materials['materials']=[m for m in materials['materials'] if m['name'] not in {v['name'] for v in new}]+new
(out/'config/materials.json').write_text(json.dumps(materials,indent=2)+'\n')
manifest={'name':'Synthetic medical-fluid kit','bodies':index,'dimensions_mm':[480,480,900],'materials':[v['name'] for v in new]+['water'],'purpose':'Original teaching geometry, not a real device design. Material densities and chemical formulas are idealized declared inputs. Thin parts may require finer meshes for resolved material metrics.'}
(out/'medical_kit.json').write_text(json.dumps(manifest,indent=2)+'\n');print(manifest)
