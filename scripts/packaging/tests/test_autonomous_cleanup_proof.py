"""Packaging must recompute the full cleanup proof retained by final evidence."""
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('cleanup_packaging_test', Path(__file__).resolve().parents[1]/'scripts/package_video.py')
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


class AutonomousCleanupProofTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = {'path':str(self.root/'clean.mp4'),'sha256':'clean-sha'}
        self.render = {'audio_cleanup':{'policy':'spoken-audio-cleanup/v1','segments':[]}}
        self.render_path = self.root/'render.json'
        self.render_path.write_text(json.dumps(self.render))
        self.clean_path = self.root/'clean.json'
        self.clean_path.write_text(json.dumps({'results':[{'plan_id':'P1','output':self.output,
            'render_evidence':{'path':str(self.render_path),'sha256':packaging.sha(self.render_path)}}]}))
        self.qc_path = self.root/'qc.json'
        self.qc_path.write_text(json.dumps({'decision':'pass','results':[{'plan_id':'P1','text_match':True,'output':self.output}]}))
        self.manifest = {'controller_validation_path':str(self.qc_path),'controller_validation_sha256':packaging.sha(self.qc_path),
            'clean_delivery_path':str(self.clean_path),'clean_delivery_sha256':packaging.sha(self.clean_path)}
        self.item = {'plan_id':'P1','input':self.output,'output_path':str(self.root/'final.mp4')}
        self.proof = {'decision':'pass','audio_cleanup':{'decision':'pass','ranges':[]}}
        self.measured = {'speech_preserved':True,'mix_evidence':self.proof}
        def require_ref(ref, label):
            path = Path(ref['path'])
            if packaging.sha(path) != ref['sha256']:
                raise ValueError(label+' hash changed')
            return path
        self.verifier = types.SimpleNamespace(pcm=Mock(return_value='actual PCM'),read=packaging.read,
            require_ref=require_ref,delivery_mix_evidence=Mock(return_value=self.proof))
        loader = types.SimpleNamespace(exec_module=lambda module:None)
        for name, val in [('spec_from_file_location',types.SimpleNamespace(loader=loader)),('module_from_spec',self.verifier)]:
            mock = patch.object(packaging.importlib.util,name,return_value=val)
            mock.start()
            self.addCleanup(mock.stop)

    def test_cleanup_record_recomputed_from_bound_render(self):
        self.assertTrue(packaging.verify_autonomous_speech(self.measured,self.item,self.manifest))
        self.verifier.delivery_mix_evidence.assert_called_once_with('actual PCM',self.item,self.render)

    def test_changed_clean_manifest_is_rejected(self):
        self.clean_path.write_text('{}')
        self.assertFalse(packaging.verify_autonomous_speech(self.measured,self.item,self.manifest))
        self.verifier.delivery_mix_evidence.assert_not_called()

    def test_changed_render_is_rejected(self):
        self.render_path.write_text('{}')
        with self.assertRaises(ValueError):
            packaging.verify_autonomous_speech(self.measured,self.item,self.manifest)
        self.verifier.delivery_mix_evidence.assert_not_called()

    def test_different_clean_output_is_rejected(self):
        clean=packaging.read(self.clean_path)
        clean['results'][0]['output']['sha256']='different-clean'
        self.clean_path.write_text(json.dumps(clean))
        self.manifest['clean_delivery_sha256']=packaging.sha(self.clean_path)
        self.assertFalse(packaging.verify_autonomous_speech(self.measured,self.item,self.manifest))
        self.verifier.delivery_mix_evidence.assert_not_called()

    def test_failed_cleanup_recomputation_is_rejected(self):
        self.verifier.delivery_mix_evidence.return_value={'decision':'reject','audio_cleanup':{'decision':'reject'}}
        self.assertFalse(packaging.verify_autonomous_speech(self.measured,self.item,self.manifest))

    def test_cleanup_free_historical_proof_keeps_previous_call(self):
        proof={'decision':'pass','basis':'original'}
        self.verifier.delivery_mix_evidence.return_value=proof
        self.assertTrue(packaging.verify_autonomous_speech({'speech_preserved':True,'mix_evidence':proof},self.item,self.manifest))
        self.verifier.delivery_mix_evidence.assert_called_once_with('actual PCM',self.item)


if __name__ == '__main__':
    unittest.main()
