#!/usr/bin/env python3
"""Controlled gateway integration checks. No real provider inference or OAuth."""
import argparse,json,sys,subprocess,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'_engine'))
from gateway import API,Error,read,private_write
import admin,copilot
state=ROOT/'private/pilot';credentials=read(state/'admin.json');api=API(credentials['gateway'],credentials['key'])

def counts():
    r=subprocess.run(['docker','compose','-f',str(ROOT/'pilot/compose.yaml'),'exec','-T','fixture','python','-c',"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080').read().decode())"],capture_output=True,text=True,check=True)
    return json.loads(r.stdout)

def fixture_key(name,models,disable):
    path=state/(name+'.json')
    if path.exists():
        personal=API(api.url,read(path)['key']);info=personal.call('/key/info')['info']
        if (info.get('metadata') or {}).get('owner')!='copilot-only-pilot':raise RuntimeError('Fixture key ownership mismatch')
        routes=copilot.required_routes(copilot.CREDENTIAL,'chat')
        if info.get('allowed_routes')!=routes:api.call('/key/update','POST',{'key':personal.key,'allowed_routes':routes})
        return personal
    uid='copilot-pilot-'+name
    user=api.call('/user/info?user_id='+uid,missing=True)
    if not user or not user.get('user_info'):
        api.call('/user/new','POST',{'user_id':uid,'user_role':'internal_user','auto_create_key':False,'models':models,'metadata':{'owner':'copilot-only-pilot'},'send_invite_email':False})
    key=api.call('/key/generate','POST',{'user_id':uid,'models':models,'allowed_routes':copilot.required_routes(copilot.CREDENTIAL,'chat'),'metadata':{'owner':'copilot-only-pilot','disable_fallbacks':disable},'send_invite_email':False})
    private_write(path,{'key':key['key'],'user_id':uid})
    return API(api.url,key['key'])

def inference(client,model,stream=False):
    try:
        client.call('/v1/chat/completions','POST',{'model':model,'messages':[{'role':'user','content':'LOCAL_SYNTHETIC_CHECK'}],'max_tokens':8,'disable_fallbacks':False,'stream':stream})
        return True
    except Error:return False

def failure_category(client,model):
    request=urllib.request.Request(client.url+'/v1/chat/completions',
        data=json.dumps({'model':model,'messages':[{'role':'user','content':'LOCAL_SYNTHETIC_CHECK'}],'max_tokens':8}).encode(),
        headers={'Authorization':'Bearer '+client.key,'Content-Type':'application/json'})
    try:
        with client.opener.open(request,timeout=30):raise AssertionError('Expected failure')
    except urllib.error.HTTPError as error:
        message=json.load(error).get('error',{}).get('message','')
        return error.code,[kind for kind in ('ContextWindowExceededError','ContentPolicyViolationError','Connect GitHub Copilot') if kind in message]

def main():
    global api
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=4198);args=parser.parse_args()
    api=API('http://127.0.0.1:'+str(args.port),credentials['key'])
    for _ in range(60):
        try:
            if api.call('/health/readiness').get('status')=='healthy':break
        except Error:pass
        time.sleep(2)
    else:raise RuntimeError('Gateway readiness failed')
    c=copilot.catalog(['claude-pilot=claude-sonnet-5.5']);c['server_config']=state/'config.yaml'
    before=admin.model_rows(api)
    before_router=api.call('/router/settings')['current_values']
    copilot.prepare(api,c,False);copilot.prepare(api,c,True);copilot.prepare(api,c,True)
    after=admin.model_rows(api)
    assert before==after,'Preparation changed existing routes'
    models=['local-generic','local-default','local-context','local-content','local-alternate','local-stream','claude-pilot','claude-missing']
    direct=fixture_key('direct-v3',models,True);legacy=fixture_key('legacy-v3',models,False)
    results={'version':api.call('/health/readiness/details')['litellm_version'],'real_provider_requests':0,'checks':{},'alternate_target_deltas':{},'failure_categories':{}}
    for model in ['local-generic','local-default','local-context','local-content']:
        n=counts().get('alternate',0)
        assert not inference(direct,model),model+' unexpectedly succeeded'
        assert counts().get('alternate',0)==n,model+' called alternate despite disable_fallbacks'
        if model in ('local-context','local-content'):
            expected='ContextWindowExceededError' if model=='local-context' else 'ContentPolicyViolationError'
            assert expected in failure_category(direct,model)[1],'Wrong synthetic failure category'
            assert counts().get('alternate',0)==n,'Category check reached alternate'
        results['checks'][model+'_no_fallback']='passed'
        assert inference(legacy,model),model+' legacy fallback failed'
        assert counts().get('alternate',0)>n,model+' did not reach alternate'
        results['checks'][model+'_legacy_preserved']='passed'
        results['alternate_target_deltas'][model]={'disabled_key':0,'control_key':counts().get('alternate',0)-n}
        if model in ('local-context','local-content'):results['failure_categories'][model]=failure_category(direct,model)[1]
    for client,expect_alternate in ((direct,False),(legacy,True)):
        n=counts().get('alternate',0)
        request=urllib.request.Request(client.url+'/v1/chat/completions',
            data=json.dumps({'model':'local-stream','messages':[{'role':'user','content':'LOCAL_SYNTHETIC_CHECK'}],'max_tokens':8,'stream':True}).encode(),
            headers={'Authorization':'Bearer '+client.key,'Content-Type':'application/json'})
        try:
            with client.opener.open(request,timeout=30) as response:payload=response.read().decode()
        except urllib.error.HTTPError as error:payload=error.read().decode()
        if expect_alternate:
            assert 'LOCAL_OK' in payload,'Stream control failed'
            assert counts().get('alternate',0)>n,'Stream control did not reach alternate'
        else:
            assert 'LOCAL_OK' not in payload and 'error' in payload.lower(),'Stream failure was not explicit'
            assert counts().get('alternate',0)==n,'Stream failure reached alternate'
    results['alternate_target_deltas']['stream_failure']={'disabled_key':0,'control_key':counts().get('alternate',0)-n}
    results['checks']['stream_failure_before_first_chunk_no_fallback']='passed'
    results['checks']['stream_failure_control_fallback']='passed'
    # Credential exists, but this user has never connected. No token exchange
    # can occur on the explicit per-user path.
    n=counts().get('alternate',0)
    assert not inference(direct,'claude-pilot'),'Unconnected user unexpectedly succeeded'
    assert counts().get('alternate',0)==n,'Disconnected user reached alternate'
    assert failure_category(direct,'claude-pilot')==(401,['Connect GitHub Copilot'])
    results['checks']['unconnected_user']='passed'
    results['alternate_target_deltas']['unconnected_user']=0
    fixture='pilot-deletion-fixture'
    if not api.call('/credentials/by_name/'+fixture,missing=True):
        api.call('/credentials','POST',{'credential_name':fixture,'credential_values':{'github_copilot_auth_type':'per_user_oauth'},'credential_info':{'custom_llm_provider':'github_copilot'}})
    api.call('/credentials/'+fixture,'DELETE')
    assert api.call('/credentials/by_name/'+fixture,missing=True) is None
    n=counts().get('alternate',0)
    assert failure_category(direct,'claude-missing')==(401,['Connect GitHub Copilot']),'Missing credential did not fail on the per-user path'
    assert counts().get('alternate',0)==n,'Deleted credential reached alternate'
    results['checks']['deleted_fixture_credential_fail_closed']='passed'
    results['alternate_target_deltas']['deleted_fixture_credential']=0
    results['failure_categories']['deleted_fixture_credential']={'http_status':401,'message_marker':'Connect GitHub Copilot'}
    restricted=fixture_key('restricted',['local-generic'],True)
    n=counts().get('alternate',0)
    assert not inference(restricted,'local-alternate'),'Unauthorized model succeeded'
    assert counts().get('alternate',0)==n,'Unauthorized model reached provider'
    results['checks']['unauthorized_model']='passed'
    results['alternate_target_deltas']['unauthorized_model']=0
    policy={'fallbacks':[{'*':['local-alternate']}],
        'context_window_fallbacks':[{'local-context':['local-alternate']}],
        'content_policy_fallbacks':[{'local-content':['local-alternate']}]}
    team_path=state/'routing-team.json'
    if team_path.exists():team=read(team_path)
    else:
        team=api.call('/team/new','POST',{'team_alias':'copilot-only-pilot-routing','models':models,
            'metadata':{'owner':'copilot-only-pilot'},'router_settings':policy})
        private_write(team_path,{'team_id':team['team_id']})
    team_info=api.call('/team/info?team_id='+team['team_id']).get('team_info',{})
    members={m.get('user_id') for m in team_info.get('members_with_roles',[])}
    for client in (direct,legacy):
        uid=client.call('/key/info')['info']['user_id']
        if uid not in members:api.call('/team/member_add','POST',{'team_id':team['team_id'],'member':{'role':'user','user_id':uid}})
    for scope in ('key','team'):
        for client in (direct,legacy):
            api.call('/key/update','POST',{'key':client.key,
                'router_settings':policy if scope=='key' else {},
                'team_id':team['team_id'] if scope=='team' else None})
        for model in ('local-generic','local-context','local-content'):
            n=counts().get('alternate',0)
            assert not inference(direct,model),scope+' failed to return error'
            assert counts().get('alternate',0)==n,scope+' policy invoked fallback'
            assert inference(legacy,model),scope+' control failed'
            assert counts().get('alternate',0)>n,scope+' control did not invoke fallback'
        results['checks'][scope+'_fallback_overrides']='passed'
        results['alternate_target_deltas'][scope+'_policy']={'disabled_key':0,'last_control_request':counts().get('alternate',0)-n}
    # Restore only these synthetic test keys to the original policy between runs.
    for client in (direct,legacy):api.call('/key/update','POST',{'key':client.key,'router_settings':{},'team_id':None})
    results['checks']['prepare_idempotent_routes_unchanged']='passed'
    assert api.call('/router/settings')['current_values']==before_router,'Global router settings changed'
    results['before_after']={'model_rows_unchanged':True,'global_router_settings_unchanged':True}
    private_write(state/'local-results.json',results)
    print(json.dumps(results,indent=2))
if __name__=='__main__':main()
