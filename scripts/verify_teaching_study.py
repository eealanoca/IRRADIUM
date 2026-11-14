"""Audit an explicitly selected public teaching workspace and write compact evidence.

Run with IRRADIUM's scientific runtime. No private workspace is searched by default.
"""
import argparse,csv,json,xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy


def field(path):
    reader=vtk.vtkXMLImageDataReader();reader.SetFileName(str(path));reader.Update()
    image=reader.GetOutput();cells=image.GetCellData()
    def array(name):
        a=cells.GetArray(name);return vtk_to_numpy(a).copy() if a is not None else None
    return vtk_to_numpy(cells.GetScalars()).copy(),array('absolute_std_dev'),array('vtkGhostType'),array('material_id')


def combined(case,positions):
    values=[];variances=[];mask=None;tags=None
    for position in positions:
        rate,std,ghost,material=field((case/position['summary']).parent/'dose_distribution.vti')
        current=np.ones(rate.shape,dtype=bool) if ghost is None else (ghost & 32)==0
        if mask is not None:assert np.array_equal(mask,current)
        mask=current;tags=material
        assert std is not None and np.isfinite(std[mask]).all()
        values.append(rate.astype(float));variances.append(std.astype(float)**2)
    total=np.sum(values,axis=0);sigma=np.sqrt(np.sum(variances,axis=0));relative=sigma[mask]/total[mask]
    return total,sigma,mask,tags,{'maximum':float(relative.max()),'p90':float(np.quantile(relative,.90)),
        'p95':float(np.quantile(relative,.95)),'p98':float(np.quantile(relative,.98)),
        'minimum_rate_kgy_h':float(total[mask].min()),'mean_rate_kgy_h':float(total[mask].mean()),
        'maximum_rate_kgy_h':float(total[mask].max()),'active_cells':int(mask.sum())}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('workspace',type=Path);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    cases=args.workspace/'irradiators/IV/Casos'
    public=[]
    for case in cases.iterdir():
        if not case.is_dir() or not (case/'manifest.json').exists():continue
        manifest=json.loads((case/'manifest.json').read_text())
        if manifest.get('teaching_example')=='svst-teaching-v1' and 'Medical-fluid' in manifest['name']:public.append(case)
    assert len(public)==1,'Select a workspace containing exactly one public complex teaching example'
    case=public[0];studies=[json.loads(p.read_text()) for p in (case/'results/studies').glob('*.json')]
    rates=next(s for s in studies if len(s['positions'])==68 and s['requested_particles_per_position']==10000000 and s['complete'])
    pilot=next(s for s in studies if len(s['positions'])==4 and s['requested_particles_per_position']==1000000)
    seeds=[ET.parse((case/p['summary']).parent/'work/settings.xml').findtext('seed') for p in rates['positions']]
    assert len(set(seeds))==68 and None not in seeds
    total,sigma,mask,tags,stats=combined(case,rates['positions'])
    low=combined(case,pilot['positions'])[-1];high=combined(case,rates['positions'][:4])[-1]
    scenarios=[json.loads(p.read_text())|{'_path':str(p)} for p in (case/'results/dose_scenarios').glob('*.json')]
    saved_sum=next(s for s in scenarios if s['run_id']==rates['run_id'] and s.get('quantity')=='rate_sum')
    saved,err,ghost,_=field(Path(saved_sum['_path']).with_suffix('.vti'))
    np.testing.assert_allclose(saved[mask],total[mask],rtol=2e-7)
    np.testing.assert_allclose(err[mask],sigma[mask],rtol=2e-7)
    forward=next(s for s in scenarios if s['run_id']==rates['run_id'] and s['request']['mode']=='total' and s['request']['value']==204)
    inverse=next(s for s in scenarios if s['run_id']==rates['run_id'] and s['request']['mode']=='target_dose' and s['request']['target_metric']=='mean')
    target=next(s for s in scenarios if s['run_id']==rates['run_id'] and s['request']['mode']=='target_dose' and s['request']['target_metric']=='minimum')
    assert abs(inverse['total_minutes']-204)<.001
    assert abs(target['times_minutes'][0]-60*2.5/stats['minimum_rate_kgy_h'])<1e-8
    material_stats={}
    for material in rates['positions'][0]['materials']:
        selected=mask & (tags==material['id'])
        if not selected.any():material_stats[material['name']]={'dominant_voxels':0};continue
        relative=sigma[selected]/total[selected]
        material_stats[material['name']]={'dominant_voxels':int(selected.sum()),'mean_rate_sum_kgy_h':float(total[selected].mean()),'maximum_relative_error':float(relative.max())}
    summaries=[json.loads((case/p['summary']).read_text()) for p in rates['positions']]
    result={'example':'Original synthetic 54-body medical-fluid kit; reconstructed SVST route',
        'study_run_id':rates['run_id'],'positions':68,'histories_per_position':10000000,'histories_total':sum(p['simulated_particles'] for p in rates['positions']),
        'cell_size_cm':rates['cell_size_cm'],'dimensions_mm':[480,480,900],'date':rates['activity_date'],'occupancy':rates['occupancy'],
        'variance_method':rates['variance_reduction'],'directed_fraction':rates['source_mixture_fraction'],
        'elapsed_seconds':rates['wall_seconds'],'transport_seconds_sum':sum(s['transport_wall_seconds'] for s in summaries),'threads':summaries[0]['cpu_threads_used'],
        'unique_seeds':len(set(seeds)),'mesh_cells':summaries[0]['total_cells'],'accumulated_statistics':stats,
        'materials':material_stats,'pilot_4_positions':{'histories_per_position':1000000,'elapsed_seconds':pilot['wall_seconds'],**low},
        'same_4_positions_high_statistics':{'histories_per_position':10000000,**high},
        'forward_204_minutes':{'mean_dose_kgy':forward['dose_kgy'],'minimum_dose_kgy':stats['minimum_rate_kgy_h']*.05,'maximum_dose_kgy':stats['maximum_rate_kgy_h']*.05},
        'inverse_mean_total_minutes':inverse['total_minutes'],
        'target_minimum_2_5_kgy':{'minutes_per_position':target['times_minutes'][0],'total_minutes':target['total_minutes'],'mean_dose_kgy':target['dose_kgy']},
        'estimated_histories_for_2_percent_per_position':int(np.ceil(1e7*(stats['maximum']/.02)**2/1e6)*1e6),
        'checks':{'saved_sum_matches_independent_voxel_sum':True,'saved_sigma_matches_independent_quadrature':True,'inverse_round_trip':True,'target_minimum_time':True},
        'limitations':['2% maximum target NOT met in this run','2 cm mesh does not resolve separate dominant voxels in silicone and steel','OpenMC TTB does not explicitly track electron paths','Commercial coordinates/source inventory/turns are didactic assumptions','No experimental operational validation']}
    det_case=next(p for p in cases.iterdir() if 'Calibraci-n-determinista' in p.name)
    mc_case=next(p for p in cases.iterdir() if 'Calibraci-n-en-aire-OpenMC' in p.name)
    deterministic=json.loads((det_case/'results/route_rates.json').read_text());mc=json.loads((mc_case/'results/route_rates.json').read_text())
    assert len(mc['positions'])==68 and mc['complete']
    dm=np.array([p['mean_kgy_h'] for p in deterministic['positions']]);mm=np.array([p['mean_kgy_h'] for p in mc['positions']]);difference=(mm/dm-1)*100
    result['air_comparison']={'positions':68,'histories_per_position':mc['requested_particles_per_position'],'cell_size_cm':mc['cell_size_cm'],
        'elapsed_seconds':mc['wall_seconds'],'deterministic_rate_sum_kgy_h':float(dm.sum()),'monte_carlo_rate_sum_kgy_h':float(mm.sum()),
        'sum_difference_percent':float((mm.sum()/dm.sum()-1)*100),'maximum_absolute_position_mean_difference_percent':float(abs(difference).max()),
        'quantity':'Alanine response in dry air, not absorbed dose to air','run_id':mc['run_id'],'same_run_resumed_after_pause':True}
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'verification.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    with (args.output/'position-dose-rates.csv').open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['position','yaw_degrees','mean_kgy_h','minimum_kgy_h','maximum_kgy_h','relative_error_maximum'])
        for p in rates['positions']:writer.writerow([p[k] for k in ['name','yaw_degrees','mean_kgy_h','minimum_kgy_h','maximum_kgy_h','relative_error_maximum']])
    with (args.output/'air-comparison.csv').open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['position','deterministic_kgy_h','openmc_kgy_h','difference_percent'])
        writer.writerows(zip([p['name'] for p in deterministic['positions']],dm,mm,difference,strict=True))
    print(json.dumps({'histories':result['histories_total'],'maximum_relative_error':stats['maximum'],'checks':result['checks'],'air_comparison':result['air_comparison']},indent=2))

if __name__=='__main__':main()
