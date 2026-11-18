"""Direct Copilot onboarding. No provider requests or client changes on connect.

1.108.0 needs file-managed models with an explicit per-user marker: its
management API rejects that marker and missing credentials otherwise fail open.
"""
import argparse, getpass, json, os, re
from pathlib import Path
from urllib.parse import quote
from gateway import API, Error, connected, key_info, origin, private_write, read
import admin, developer

MODE='copilot-only'
VERSION='1.108.0'
CREDENTIAL='github-copilot-user'

def catalog(selections):
    if not selections:raise Error('Copilot-only requires explicit --model alias=provider-model selection; verify entitlement first.')
    models=[];seen=set()
    for selection in selections:
        alias,sep,model=selection.partition('=')
        model=model.removeprefix('github_copilot/')
        if not sep or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',alias) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',model):
            raise Error('Use an explicit alias=Copilot-model-ID (no wildcards).')
        if alias in seen:raise Error('Duplicate Copilot alias.')
        seen.add(alias)
        models.append({'model_name':alias,'litellm_params':{'model':'github_copilot/'+model,
            'litellm_credential_name':CREDENTIAL,'github_copilot_auth_type':'per_user_oauth'}})
    return {'mode':MODE,'credential':CREDENTIAL,'models':models,'fallbacks':[]}

def fragment(c):
    # JSON is valid YAML; merge model_list into the existing server config.
    return {'model_list':c['models']}

def check_routes(api,c):
    config_path=c.get('server_config')
    if config_path:
        # This must be the authoritative active deployment configuration, not a
        # generated fragment. JSON avoids adding a YAML dependency to the CLI.
        config=read(config_path)
        configured=config.get('model_list',[])
        selected={m['model_name'] for m in c['models']}
        for aliases in (config.get('router_settings',{}).get('model_group_alias',{}),config.get('litellm_settings',{}).get('model_alias_map',{})):
            if selected.intersection(aliases or {}):raise Error('A server alias remaps a selected Copilot route; resolve the conflict before onboarding.')
        for wanted in c['models']:
            matches=[r for r in configured if r.get('model_name')==wanted['model_name']]
            if len(matches)!=1 or any(matches[0].get('litellm_params',{}).get(k)!=v for k,v in wanted['litellm_params'].items()):
                raise Error('Active server configuration does not match the explicit per-user route.')
        if api.call('/health/readiness/details').get('litellm_version')!=VERSION:
            raise Error('Only audited LiteLLM '+VERSION+' is supported. Validate another version before onboarding.')
    rows=admin.model_rows(api)
    for wanted in c['models']:
        found=[r for r in rows if r.get('model_name')==wanted['model_name']]
        if len(found)!=1 or any(not (found[0].get('litellm_params',{}).get(k)==v or (config_path and k=='github_copilot_auth_type' and found[0].get('litellm_params',{}).get(k)=='per_******auth')) for k,v in wanted['litellm_params'].items()):
            raise Error('Unsafe or missing route '+wanted['model_name']+'. Merge the copilot-only config fragment through the gateway deployment process and restart. API-only model creation on LiteLLM 1.108.0 cannot guarantee failure on credential deletion; no writes performed.')
        params=found[0]['litellm_params']
        if any(params.get(k) for k in ('api_key','api_base','extra_headers','use_clientside_credentials')):
            raise Error('Copilot route has conflicting provider overrides; no writes performed.')

def prepare(api,c,apply=False):
    check_routes(api,c)
    cred=api.call('/credentials/by_name/'+quote(c['credential'],safe=''),missing=True)
    if cred and (cred.get('credential_values',{}).get('github_copilot_auth_type')!='per_user_oauth' or
                 cred.get('credential_info',{}).get('custom_llm_provider')!='github_copilot'):
        raise Error('Conflicting credential; no writes performed.')
    print('Plan: reuse explicit file-managed Copilot routes; '+('reuse' if cred else 'create')+' per-user credential. No global routing changes.')
    if apply and not cred:
        api.call('/credentials','POST',{'credential_name':c['credential'],
            'credential_values':{'github_copilot_auth_type':'per_user_oauth'},
            'credential_info':{'custom_llm_provider':'github_copilot'}})
    return {'mode':MODE,'credential_created':bool(apply and not cred)}

def required_routes(credential,protocol):
    inference={'messages':['/v1/messages','/v1/messages/count_tokens'], 'chat':['/v1/chat/completions']}
    if protocol not in inference:raise Error('Supported protocols: chat or messages.')
    return inference[protocol]+['/v1/models','/key/info','/user/info','/v1/model/info',
        '/credentials/user_connections']+['/credentials/'+credential+'/user_connection'+s for s in ('','/start','/poll')]

def validate_identity(api,uid,models):
    info=key_info(api)
    if info['user_id']!=uid:raise Error('The inference key belongs to a different LiteLLM user.')
    user=api.call('/user/info').get('user_info',{})
    if user.get('user_id')!=uid or user.get('user_role') not in ('internal_user','internal_user_viewer'):
        raise Error('Use a user-bound developer virtual key, never a master/admin key.')
    if set(models).intersection(info.get('aliases') or {}):raise Error('The key remaps a selected model alias; direct routing cannot be guaranteed.')
    permitted=info.get('models') or []
    if not set(models)<=set(permitted):raise Error('Key does not explicitly authorize the selected models. Ask the administrator for a minimal permission addition.')
    return info

def enroll_existing(api,c,uid,login,key,path,protocol='chat',apply=False):
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?',login):raise Error('Invalid GitHub login.')
    prepare(api,c,False)
    models=[m['model_name'] for m in c['models']]
    personal=API(api.url,key);info=validate_identity(personal,uid,models)
    if info.get('team_id'):
        team=api.call('/team/info?team_id='+quote(info['team_id'],safe='')).get('team_info',{})
        for field in ('model_aliases','model_alias_map'):
            if set(models).intersection(team.get(field) or {}):raise Error('The team remaps a selected model alias; no writes performed.')
    routes=info.get('allowed_routes')
    if routes and not set(required_routes(c['credential'],protocol))<=set(routes):
        raise Error('Key lacks connection/inference routes. Preview the minimal additions with the administrator; permissions were not changed.')
    md=info.get('metadata') or {}
    if md.get('disable_fallbacks') is not True and set(info.get('models',[]))!=set(models):
        raise Error('A mixed-purpose key cannot be changed safely: disable_fallbacks applies to the entire key. Use an existing Copilot-scoped key or a separately approved key; nothing changed.')
    invitation={'format':2,'mode':MODE,'gateway':api.url,'user_id':uid,'github_login':login.lower(),
        'credential':c['credential'],'models':models,'key':key,'protocol':protocol,'routes':c['models'],
        'fallback_policy':{'metadata':{'disable_fallbacks':True}}}
    path=Path(path)
    if path.exists() and read(path)!=invitation:raise Error('Invitation file conflicts with this enrollment; use another filename.')
    print('Plan: reuse existing user and key, preserve budgets/expiry/permissions; '+
          ('verify' if md.get('disable_fallbacks') is True else 'add')+' key metadata.disable_fallbacks=true. Save private format-2 invitation.')
    if not apply:return invitation
    if md.get('disable_fallbacks') is not True:
        latest=key_info(personal)
        if latest!=info:raise Error('Key changed during preview; rerun enrollment.')
        api.call('/key/update','POST',{'key':key,'metadata':dict(md,disable_fallbacks=True)})
        if key_info(personal).get('metadata',{}).get('disable_fallbacks') is not True:
            raise Error('No-fallback key policy did not persist. No invitation saved.')
    private_write(path,invitation)
    return invitation

def validate(inv):
    developer.validate_invitation_shape(inv)
    if inv.get('mode')!=MODE:raise Error('Expected a copilot-only invitation.')
    api=API(inv['gateway'],inv['key']);info=validate_identity(api,inv['user_id'],inv['models'])
    if (info.get('metadata') or {}).get('disable_fallbacks') is not True:
        raise Error('Required key metadata.disable_fallbacks=true is missing; ask the administrator to repair policy.')
    return api

def state_path():
    return Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'claude-gateway/copilot-only.json'

def join(inv,no_browser=False):
    api=validate(inv)
    developer.connect(api,inv,no_browser)
    private_write(state_path(),inv)
    print('GitHub connected. No inference sent; no Claude settings or sign-in changed.')

def doctor(inv=None):
    inv=inv or read(state_path());api=validate(inv)
    conn=connected(api,inv['credential'])
    if not conn or conn.get('github_login','').lower()!=inv['github_login'].lower():
        raise Error('GitHub connection missing or mismatched. Rerun copilot-only connect with your invitation.')
    available={m.get('id') for m in api.call('/v1/models').get('data',[])}
    if not set(inv['models'])<=available:raise Error('Selected direct model is not available to this key.')
    return {'mode':MODE,'identity':'passed','connection':'passed','key_no_fallback':'passed',
            'model_access':'passed','inference':'not_run','server_route_audit':'administrator_required'}

def configure(inv,directory):
    validate(inv)
    if inv['protocol']!='messages':raise Error('Claude Code requires a messages-protocol invitation.')
    directory=Path(directory)
    if (directory/'managed-settings.json').exists() or Path('/Library/Application Support/ClaudeCode/managed-settings.json').exists():
        raise Error('Managed Claude policy detected. Ask IT to configure the approved gateway.')
    if not inv['models'][0].startswith(('claude-','anth-')):raise Error('Claude Code requires a claude- or anth- alias.')
    developer.configure(inv,directory)

def main(argv=None):
    p=argparse.ArgumentParser(description='Direct Copilot mode (no Claude subscription).')
    p.add_argument('command',choices=['fragment','prepare','enroll','connect','doctor','configure','restore'])
    p.add_argument('--server-config',type=Path,help='Authoritative active server config exported as JSON; required to verify masked authentication fields.');p.add_argument('--gateway');p.add_argument('--model',action='append');p.add_argument('--user-id')
    p.add_argument('--github-login');p.add_argument('--out',type=Path);p.add_argument('--invite',type=Path)
    p.add_argument('--protocol',choices=['chat','messages'],default='chat');p.add_argument('--apply',action='store_true')
    p.add_argument('--no-browser',action='store_true');p.add_argument('--client-dir',type=Path)
    a=p.parse_args(argv)
    if a.command=='fragment':print(json.dumps(fragment(catalog(a.model)),indent=2));return
    if a.command in ('prepare','enroll'):
        if not a.gateway:p.error('--gateway required')
        api=API(origin(a.gateway),getpass.getpass('Administrator key (hidden): '));c=catalog(a.model)
        if not a.server_config:p.error('--server-config is required: supply the active deployment configuration as JSON')
        c['server_config']=a.server_config
        if a.command=='prepare':prepare(api,c,a.apply)
        else:
            if not all([a.user_id,a.github_login,a.out]):p.error('enroll requires --user-id, --github-login, --out')
            enroll_existing(api,c,a.user_id,a.github_login,getpass.getpass('Existing user virtual key (hidden): '),a.out,a.protocol,a.apply)
    elif a.command=='restore':
        if not a.client_dir:p.error('restore requires explicit --client-dir')
        if read(a.client_dir/'gateway-onboarding.json').get('mode')!=MODE:raise Error('Client state belongs to another integration mode.')
        developer.undo(a.client_dir)
    else:
        inv=read(a.invite) if a.invite else read(state_path())
        if a.command=='connect':join(inv,a.no_browser)
        elif a.command=='doctor':print(json.dumps(doctor(inv),indent=2))
        elif a.command=='configure':
            if not a.client_dir:p.error('configure requires explicit --client-dir')
            configure(inv,a.client_dir)
