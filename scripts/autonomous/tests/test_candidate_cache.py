import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch
import numpy as np

spec=importlib.util.spec_from_file_location('cache_auto',Path(__file__).resolve().parents[1]/'scripts/autonomous_montage.py')
auto=importlib.util.module_from_spec(spec);spec.loader.exec_module(auto)

class CandidateCacheTests(TestCase):
    def test_same_media_reuses_checks_but_gain_change_does_not(self):
        segment={'source_path':'source.mp4','source_sha256':'source-hash','source_in_frame':30,
                 'source_out_frame_exclusive':90,'source_fps_num':60,'source_fps_den':1,
                 'audio_gain_db':-3,'text':'完整台词'}
        plan={'outputs':[{'plan_id':'A','segments':[segment]}, {'plan_id':'B','segments':[dict(segment)]},
                         {'plan_id':'C','segments':[dict(segment,audio_gain_db=-6)]}]}
        state={'phase':'prepared','source_index':{'path':'index'},'work_order':{'path':'order'},'repair_round':0}
        records={'index':{'sources':[{'source':{'sha256':'source-hash'},'asr':{'path':'asr'}}]},
                 'order':{'requested_outputs':3},'plan':plan,'asr':{'asr':{'text':'完整台词'}}}
        saved=[]
        with patch.multiple(auto,state=lambda _:state,require_ref=lambda r,_:Path(r['path']),
                            read=lambda p:records[str(p)],check_plan=lambda *a:[],load_model=lambda:None,
                            run=lambda *a:None,pcm=lambda _:np.zeros(100),
                            audio_metrics=lambda _:{'head_40ms_dbfs':-90,'tail_40ms_dbfs':-90,'clipped_samples':0},
                            isolated_transients=lambda _:0,video=lambda _:{'frames':200},frames=lambda *a:[],
                            continuous_visual=lambda *a:{'path':'continuous','sha256':'visual'},
                            visual_boundary_metrics=lambda *a:{'decision':'pass'},ref=lambda p:{'path':str(p)},
                            batch_fields=lambda *a:{},write=lambda p,v:saved.append(v),save=lambda *a:None), \
             patch.object(auto.EDITING,'summary',lambda o:{'plan_id':o['plan_id']}), \
             patch.object(auto,'transcribe',return_value={'text':'完整台词'}) as transcribe:
            import tempfile
            with tempfile.TemporaryDirectory() as folder:
                auto.plan_evidence(SimpleNamespace(job_dir=Path(folder),plan=Path('plan')))
        self.assertEqual(transcribe.call_count,2)
        rows=saved[-1]['results']
        self.assertEqual([r['plan_id'] for r in rows],['A','B','C'])
        self.assertEqual(rows[0]['pcm'],rows[1]['pcm'])
        self.assertNotEqual(rows[0]['pcm'],rows[2]['pcm'])
        self.assertTrue(all(r['decision']=='pass' for r in rows))

if __name__=='__main__':main()
