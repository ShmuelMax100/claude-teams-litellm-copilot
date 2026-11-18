import copy,json,sys,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'_engine'))
import copilot,developer,admin
from gateway import Error,load_catalog,read,private_write

class Fake:
    url='https://gateway.example'
    def __init__(self):
        self.c=copilot.catalog(['claude-direct=current-model']);self.rows=copy.deepcopy(self.c['models']);self.cred=None;self.writes=[]
        self.info={'user_id':'existing','models':['claude-direct'],'metadata':{'customer':'keep'},'router_settings':{'fallbacks':[{'unrelated':['backup']}]}}
    def call(self,path,method='GET',body=None,missing=False):
        if method!='GET':self.writes.append((path,copy.deepcopy(body)))
        if path=='/health/readiness/details':return {'litellm_version':'1.108.0'}
        if path.startswith('/v2/model/info'):return {'data':self.rows}
        if path.startswith('/credentials/by_name/'):return self.cred
        if path=='/credentials':self.cred=body;return body
        if path=='/key/info':return {'info':copy.deepcopy(self.info)}
        if path=='/user/info':return {'user_info':{'user_id':'existing','user_role':'internal_user','metadata':{'unrelated':'keep'}}}
        if path=='/key/update':self.info['metadata']=body['metadata'];return {}
        if path=='/credentials/user_connections':return {'connections':[{'credential_name':copilot.CREDENTIAL,'connected':True,'github_login':'example'}]}
        if path=='/v1/models':return {'data':[{'id':'claude-direct'}]}
        raise AssertionError(path)

class CopilotTests(unittest.TestCase):
    def setUp(self):self.a=Fake();self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def invite(self,apply=True):
        with patch.object(copilot,'API',return_value=self.a):
            return copilot.enroll_existing(self.a,self.a.c,'existing','example','sk-unit-test-placeholder',self.root/'invite.json',apply=apply)
    def test_legacy_default_unchanged(self):
        self.assertEqual(load_catalog(),load_catalog(mode='legacy'))
        self.assertIn('anthropic/*',[m['model_name'] for m in load_catalog()['models']])
    def test_explicit_catalog_no_legacy_overrides(self):
        c=load_catalog(mode='copilot-only',selections=['custom=model-now'])
        self.assertEqual(c['fallbacks'],[]);self.assertEqual(len(c['models']),1)
        self.assertNotIn('max_retries',c['models'][0]['litellm_params']);self.assertNotIn('model_info',c['models'][0])
        for bad in (None,[],['x=*'],['x=m','x=n'],['anthropic/*=x']):
            with self.assertRaises(Error):copilot.catalog(bad)
    def test_prepare_preview_and_idempotence(self):
        copilot.prepare(self.a,self.a.c);self.assertEqual(self.a.writes,[])
        copilot.prepare(self.a,self.a.c,True);copilot.prepare(self.a,self.a.c,True)
        self.assertEqual(len(self.a.writes),1)
    def test_missing_or_unsafe_route_no_writes(self):
        for field in ('model','github_copilot_auth_type','litellm_credential_name'):
            a=Fake();a.rows[0]['litellm_params'].pop(field)
            with self.assertRaises(Error):copilot.prepare(a,a.c,True)
            self.assertEqual(a.writes,[])
    def test_conflicting_credential_no_writes(self):
        self.a.cred={'credential_values':{'github_copilot_auth_type':'shared'}}
        with self.assertRaises(Error):copilot.prepare(self.a,self.a.c,True)
        self.assertFalse(self.a.writes)
    def test_existing_identity_preserves_metadata_router_and_permissions(self):
        before=copy.deepcopy(self.a.info);inv=self.invite()
        self.assertEqual(inv['user_id'],'existing');self.assertEqual(self.a.info['router_settings'],before['router_settings'])
        self.assertEqual(self.a.writes,[('/key/update',{'key':'sk-unit-test-placeholder','metadata':{'customer':'keep','disable_fallbacks':True}})])
        self.invite();self.assertEqual(len(self.a.writes),1)
    def test_enroll_preview_no_writes_or_file(self):
        self.invite(False);self.assertFalse(self.a.writes);self.assertFalse((self.root/'invite.json').exists())
    def test_mixed_key_rejected(self):
        self.a.info['models'].append('unrelated')
        with self.assertRaises(Error):self.invite()
        self.assertFalse(self.a.writes)
    def test_wrong_owner_or_master_rejected(self):
        for uid in ('other',None):
            self.a.info['user_id']=uid
            with self.assertRaises(Error):self.invite()
        self.assertFalse(self.a.writes)
    def test_unauthorized_model_rejected(self):
        self.a.info['models']=[]
        with self.assertRaises(Error):self.invite()
    def test_format_compatibility(self):
        inv=self.invite();developer.validate_invitation_shape(inv)
        with self.assertRaises(Error):developer.validate_invitation_shape(dict(inv,format=1))
        with self.assertRaises(Error):developer.validate_invitation_shape(dict(inv,mode='legacy'))
        old=dict(inv,format=1);old.pop('mode');developer.validate_invitation_shape(old)
    def test_connect_has_no_claude_dependency_or_inference(self):
        inv=self.invite()
        with patch.object(copilot,'API',return_value=self.a),patch.object(copilot,'state_path',return_value=self.root/'state.json'),patch.object(developer,'preflight',side_effect=AssertionError),patch.object(developer,'configure',side_effect=AssertionError),patch.object(developer,'probe_copilot',side_effect=AssertionError):
            developer.join(inv,True)
        self.assertEqual(read(self.root/'state.json')['mode'],'copilot-only')
    def test_doctor_and_disconnected(self):
        inv=self.invite()
        with patch.object(copilot,'API',return_value=self.a):
            self.assertEqual(copilot.doctor(inv)['inference'],'not_run')
            with patch.object(copilot,'connected',return_value=None),self.assertRaisesRegex(Error,'missing'):copilot.doctor(inv)
    def test_policy_removed_rejected(self):
        inv=self.invite();self.a.info['metadata'].pop('disable_fallbacks')
        with patch.object(copilot,'API',return_value=self.a),self.assertRaises(Error):copilot.validate(inv)
    def test_optional_client_direct_auth_and_restore(self):
        inv=self.invite();inv['protocol']='messages'
        directory=self.root/'client';before={'theme':'dark','permissions':{'deny':['Bash(rm *)']}}
        private_write(directory/'settings.json',before)
        with patch.object(copilot,'validate'):copilot.configure(inv,directory)
        after=read(directory/'settings.json');self.assertEqual(after['model'],'claude-direct')
        self.assertEqual(after['env']['ANTHROPIC_AUTH_TOKEN'],inv['key']);self.assertEqual(after['permissions'],before['permissions'])
        developer.undo(directory);self.assertEqual(read(directory/'settings.json'),before)
    def test_expired_consent(self):
        class Flow:
            def call(self,path,*args):
                if path.endswith('start'):return {'verification_uri':'https://github.com/login/device','interval':1,'expires_in':5,'user_code':'TEST','flow_handle':'test'}
                return {'status':'expired'}
        with patch.object(developer,'connected',return_value=None),patch.object(developer.time,'sleep'),self.assertRaises(Error):
            developer.connect(Flow(),{'credential':'test','github_login':'example'},True)
    def test_shipped_entrypoint(self):
        p=subprocess.run([str(Path(__file__).resolve().parents[1]/'setup'),'--mode','copilot-only','fragment','--model','chosen=model-now'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0);self.assertEqual(json.loads(p.stdout)['model_list'][0]['model_name'],'chosen')

    def test_null_poll_interval_and_no_revocation_on_wrong_account(self):
        class Flow:
            def __init__(self):self.n=0
            def call(self,path,method='POST',body=None):
                if method=='DELETE':raise AssertionError('Connection revocation requires explicit approval')
                if path.endswith('start'):return {'verification_uri':'https://github.com/login/device','interval':1,'expires_in':30,'user_code':'TEST','flow_handle':'test'}
                self.n+=1
                return {'status':'pending','interval':None} if self.n==1 else {'status':'connected','github_login':'wrong'}
        with patch.object(developer,'connected',return_value=None),patch.object(developer.time,'sleep'),self.assertRaisesRegex(Error,'Wrong GitHub'):
            developer.connect(Flow(),{'credential':'test','github_login':'expected','mode':'copilot-only'},True)
    def test_managed_client_policy_and_later_edits(self):
        inv=self.invite();inv['protocol']='messages';d=self.root/'client'
        private_write(d/'managed-settings.json',{})
        with patch.object(copilot,'validate'),self.assertRaisesRegex(Error,'Managed'):copilot.configure(inv,d)
        (d/'managed-settings.json').unlink()
        with patch.object(copilot,'validate'):copilot.configure(inv,d)
        private_write(d/'settings.json',{'theme':'later'})
        with self.assertRaisesRegex(Error,'Settings changed'):developer.undo(d)
    def test_invitation_conflict_does_not_change_key(self):
        private_write(self.root/'invite.json',{'belongs':'someone-else'})
        with self.assertRaises(Error):self.invite()
        self.assertFalse(self.a.writes)

    def test_masked_marker_requires_matching_active_config(self):
        self.a.rows[0]['litellm_params']['github_copilot_auth_type']='per_******auth'
        with self.assertRaises(Error):copilot.prepare(self.a,self.a.c)
        private_write(self.root/'active.json',{'model_list':self.a.c['models']})
        self.a.c['server_config']=self.root/'active.json'
        copilot.prepare(self.a,self.a.c)
        bad=copy.deepcopy(self.a.c['models']);bad[0]['litellm_params'].pop('github_copilot_auth_type')
        private_write(self.root/'active.json',{'model_list':bad})
        with self.assertRaises(Error):copilot.prepare(self.a,self.a.c,True)
        self.assertFalse(self.a.writes)
    def test_api_disconnect_is_actionable(self):
        import http.client
        from gateway import API
        api=API('https://gateway.example','sk-unit-test-placeholder')
        with patch.object(api.opener,'open',side_effect=http.client.RemoteDisconnected),self.assertRaisesRegex(Error,'could not be reached'):
            api.call('/health/readiness')

    def test_key_alias_remapping_rejected(self):
        self.a.info['aliases']={'claude-direct':'other-provider'}
        with self.assertRaisesRegex(Error,'remaps'):self.invite()
        self.assertFalse(self.a.writes)
    def test_server_alias_remapping_rejected(self):
        private_write(self.root/'active.json',{'model_list':self.a.c['models'],'router_settings':{'model_group_alias':{'claude-direct':'other-provider'}}})
        self.a.c['server_config']=self.root/'active.json'
        with self.assertRaisesRegex(Error,'remaps'):copilot.prepare(self.a,self.a.c,True)
        self.assertFalse(self.a.writes)
