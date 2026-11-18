#!/usr/bin/env python3
"""Initialize isolated private state; never overwrite existing secrets/config."""
import json,os,secrets,socket,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'_engine'))
from gateway import private_write
import copilot
state=ROOT/'private/pilot';state.mkdir(parents=True,exist_ok=True);state.chmod(0o700)
if not (state/'admin.json').exists():
    with socket.socket() as sock:
        try:sock.bind(('127.0.0.1',4198))
        except OSError:raise SystemExit('Port 4198 is occupied. Stop and choose an unused loopback port before initializing; no secrets written.')
    if any((state/n).exists() for n in ('db.env','gateway.env')):
        raise SystemExit('Partial secret state exists. Reconcile it manually; refusing to replace credentials.')
    password=secrets.token_hex(24);key='sk-'+secrets.token_hex(32)
    for name,value in {'db.env':f'POSTGRES_USER=pilot\nPOSTGRES_DB=pilot\nPOSTGRES_PASSWORD={password}\n',
        'gateway.env':f'DATABASE_URL=postgresql://pilot:{password}@db:5432/pilot\nLITELLM_MASTER_KEY={key}\nLITELLM_SALT_KEY={secrets.token_hex(32)}\nSTORE_MODEL_IN_DB=True\n'}.items():
        fd=os.open(state/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(value)
    private_write(state/'admin.json',{'gateway':'http://127.0.0.1:4198','key':key})
config={'model_list':copilot.catalog(['claude-pilot=claude-sonnet-5.5'])['models'],
    'general_settings':{'master_key':'os.environ/LITELLM_MASTER_KEY','store_model_in_db':True,'trusted_proxy_ranges':[]},
    'router_settings':{'num_retries':0,'fallbacks':[{'local-generic':['local-alternate']},{'*':['local-alternate']}],
        'default_fallbacks':['local-alternate'],'context_window_fallbacks':[{'local-context':['local-alternate']}],
        'content_policy_fallbacks':[{'local-content':['local-alternate']}]}}
for alias,provider in [('generic','generic'),('default','generic'),('stream','stream-error'),('context','context'),('content','content'),('alternate','alternate')]:
    config['model_list'].append({'model_name':'local-'+alias,'litellm_params':{'model':'openai/'+provider,
        'api_base':'http://fixture:8080/v1','api_key':'synthetic','max_retries':0}})
config['model_list'].append({'model_name':'claude-missing','litellm_params':{'model':'github_copilot/claude-sonnet-5.5',
    'litellm_credential_name':'pilot-deletion-fixture','github_copilot_auth_type':'per_user_oauth'}})
if not (state/'config.yaml').exists():private_write(state/'config.yaml',config)
print('Protected pilot state ready. Existing files were preserved. The Copilot model is a placeholder until entitlement is verified.')
