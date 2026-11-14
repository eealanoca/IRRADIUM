"""Generate the original, openly documented teaching reconstruction."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'app/examples/commercial_svst'
def write(name,value):
 p=OUT/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
# Teaching labels S1..S34 and I1..I34; not a numerical digitization of Fig. 1.
positions=[]
for level,z in [('S',500),('I',-500)]:
 rows=[(8,800,True),(8,1400,False),(9,-800,True),(9,-1400,False)]
 if level=='I':rows=[(9,1400,True),(9,800,False),(8,-800,True),(8,-1400,False)]
 for count,y,reverse in rows:
  xs=[(i-(count-1)/2)*550 for i in range(count)]
  if reverse:xs.reverse()
  yaw=180 if reverse else 0
  for x in xs:
   positions.append({'name':f'{level}{1+sum(p["name"].startswith(level) for p in positions)}','center_mm':[x,y,z],'yaw_degrees':yaw})
route={'positions':positions,'calculation_positions':None,'radionuclide':'Co-60','cpu_cores':-1,'particles':100000000,'batches':20,'cell_size_cm':2,'relative_error_target':.05,'accumulated_error_target':.02,'interval_max_particles':300000000,'interval_time_limit_seconds':18000,'dose_tally':'heating','occupancy':'isolated','position_reference':'center','variance_reduction':'source_mixture','source_mixture_fraction':.5,'cad_offset_degrees':0,'deterministic_samples':10000}
write('config/route_parameters.json',route)
sources=json.loads((ROOT/'app/data/config/sources.json').read_text())
sources['sources']=[{'identifier':f'DEMO-EQUIV-{i+1:02d}','lower_point':[float(x),0,-900.],'upper_point':[float(x),0,900.],'intensity':280000*3.7e10/12,'initial_reading_date':'2026-01-01'} for i,x in enumerate([(i-5.5)*220 for i in range(12)])]
sources['metadata']={'purpose':'teaching-reconstruction','notice':'12 synthetic equivalent cylinders, NOT the commercial source inventory. Total historical reference activity adopted as a teaching input; no assertion of actual 2026 activity.'}
write('config/sources.json',sources)
materials=json.loads((ROOT/'app/data/config/materials.json').read_text())
airload={**materials['materials'][0],'name':'airload'}
water={'name':'water','elements':{'H':2,'O':1},'density':1.,'fraction_type':'atomic'}
# Preserve additional teaching materials when regenerating the reference.
existing_path=OUT/'config/materials.json'
existing=json.loads(existing_path.read_text()).get('materials',[]) if existing_path.exists() else []
by_name={m['name']:m for m in existing+materials['materials']+[airload,water]}
materials['materials']=list(by_name.values())
write('config/materials.json',materials)
write('provenance.json',{'id':'svst-teaching-v1','name':'SVST-Co-60/B · reconstrucción didáctica','version':1,'category':'IV','source_url':'https://www.irjet.net/archives/V2/i2/Irjet-v2i2196.pdf','citation':'Tran Van Hung (2015), IRJET 2(2), 1075–1079.','published':{'positions':68,'levels':2,'rows_per_level':4,'nominal_tote_mm':[500,500,900],'inner_tote_mm':[480,480,900],'dwell_seconds':180,'historical_reference_activity_kci':280,'source_racks':3,'modules_per_rack':4},'reconstructed':{'center_spacing_x_mm':550,'row_y_mm':[800,1400,-800,-1400],'level_centers_z_mm':[500,-500],'rotation_rule':'180 degrees on reversing X direction; illustrative assumption, NOT verified mechanical rotations','sources':'12 equivalent uniform cylinders, diameter 11 mm; endpoints and activity distribution synthetic','load':'homogeneous water 480 x 480 x 900 mm; alternative air-load reference','date':'2026-01-01 is a didactic reference date only'},'excluded':'Bunker, pool, shielding, aluminum tote and transport mechanisms are not modeled. Not a shielding study or manufacturer validation.','license':'MIT for original JSON/CAD; cited publication and commercial name retain their own rights.'})
# Fresh installations have usable materials; existing editable configuration is never replaced.
(ROOT/'app/data/config/materials.json').write_text(json.dumps(materials,indent=2)+'\n')
if __name__=='__main__':
 from cadquery import Assembly,Workplane
 for tag in ('water','airload'):
  assembly=Assembly(name=f'{tag}_000')
  assembly.add(Workplane('XY').box(480,480,900,centered=(True,True,False)),name=f'{tag}_001')
  assembly.export(str(OUT/f'{tag}.step'))
