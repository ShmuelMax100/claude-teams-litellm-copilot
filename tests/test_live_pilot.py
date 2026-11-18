import contextlib,importlib.util,io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'_engine'))
from gateway import Error,private_write,read
spec=importlib.util.spec_from_file_location('pilot_live',ROOT/'pilot/check_live.py')
live=importlib.util.module_from_spec(spec);spec.loader.exec_module(live)

class LiveBatchSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.inv=self.root/'invitation.json'
        private_write(self.inv,{'models':['claude-pilot'],'protocol':'chat','routes':[{'litellm_params':{'model':'github_copilot/claude-sonnet-5.5'}}]})
        self.api=Mock();self.api.url='http://127.0.0.1:4198'
    def tearDown(self):self.tmp.cleanup()
    def run_batch(self,*args):
        with patch.object(live,'ROOT',self.root),patch.object(sys,'argv',['check_live','--invite',str(self.inv),*args]),patch.object(live.copilot,'validate',return_value=self.api),patch.object(live.copilot,'doctor'),contextlib.redirect_stdout(io.StringIO()):
            live.main()
    def test_preview_never_sends_inference(self):
        self.run_batch();self.api.call.assert_not_called()
        self.assertFalse((self.root/'private/pilot/live-request-count.json').exists())
    def test_exhausted_budget_sends_nothing(self):
        counter=self.root/'private/pilot/live-request-count.json';private_write(counter,{'requests':10})
        with self.assertRaisesRegex(Error,'cap is exhausted'):self.run_batch('--approved')
        self.api.call.assert_not_called();self.assertEqual(read(counter)['requests'],10)
    def test_resume_completed_batch_sends_nothing(self):
        private_write(self.root/'private/pilot/live-results.json',{'direct':'passed','stream':'passed','synthetic_tool_round_trip':'passed'})
        self.run_batch('--approved','--resume');self.api.call.assert_not_called()
