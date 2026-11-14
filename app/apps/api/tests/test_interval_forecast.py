import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sim_gamma.domain.interval_forecast import estimate_interval, _from_maps
from sim_gamma.simulators.openmc.structured_postprocess import RegularMeshSpec, write_compressed_vti


class IntervalForecastTests(unittest.TestCase):
    def test_halving_target_quadruples_histories_and_transport_not_setup(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'results/studies';folder.mkdir(parents=True)
            positions=[]
            for index in range(2):
                output=root/str(index);output.mkdir()
                write_compressed_vti([1,1],RegularMeshSpec((2,1,1),(0,0,0),(4,2,2)),output/'dose_distribution.vti',relative_error=[.1,.1])
                (output/'result.json').write_text(json.dumps({'transport_wall_seconds':2,'preparation_wall_seconds':1,'postprocess_wall_seconds':1}))
                positions.append({'name':f'I{index+1}','center_mm':[index*10,0,0],'yaw_degrees':0,'summary':f'{index}/result.json','simulated_particles':1_000_000})
            config={'positions':positions,'cell_size_cm':2,'occupancy':'isolated','variance_reduction':'source_box','source_mixture_fraction':.5,'dose_tally':'heating'}
            study={**config,'transport_geometry_revision':2,'complete':True,'run_id':'pilot'}
            path=folder/'pilot.json';path.write_text(json.dumps(study))
            low=estimate_interval(root,config,{'start':0,'end':1,'accumulated_error_target':.02})
            high=estimate_interval(root,config,{'start':0,'end':1,'accumulated_error_target':.01})
            self.assertTrue(low['available']);self.assertTrue(high['provisional'])
            self.assertAlmostEqual(high['estimated_total_histories']/low['estimated_total_histories'],4,delta=.4)
            self.assertAlmostEqual((high['estimated_seconds']-4)/(low['estimated_seconds']-4),4,delta=.4)
            self.assertGreater(high['suggested_time_limit_seconds'],high['estimated_seconds'])
            path.write_text(json.dumps({**study,'transport_geometry_revision':1}))
            self.assertFalse(estimate_interval(root,config,{'start':0,'end':1,'accumulated_error_target':.005})['available'])
