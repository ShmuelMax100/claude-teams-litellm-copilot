#!/usr/bin/env python3
"""Read pilot-only routing evidence and emit an aggregate with no account data.

Run immediately after the live batch, before local control tests change the
fixture counters. No inference, token extraction or connection changes occur.
"""
import json,sys
from pathlib import Path
from urllib.parse import quote,urlsplit
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'_engine'))
from gateway import API,read,private_write
from check_local import counts

def main():
    state=ROOT/'private/pilot';s=read(state/'admin.json');inv=read(state/'human-invitation.json')
    api=API(s['gateway'],s['key'])
    rows=[r for r in api.call('/spend/logs?user_id='+quote(inv['user_id'],safe=''))
          if r.get('user')==inv['user_id'] and r.get('model_group')==inv['models'][0]]
    assert rows and len(rows)==read(state/'live-request-count.json')['requests'],'Request ledger and gateway logs differ'
    metadata=[json.loads(r['metadata']) if isinstance(r.get('metadata'),str) else r.get('metadata') or {} for r in rows]
    evidence={'logged_requests':len(rows),'successful':sum(r.get('status')=='success' for r in rows),
        'failed':sum(r.get('status')!='success' for r in rows),
        'all_same_user':all(r.get('user')==inv['user_id'] for r in rows),
        'all_github_copilot':all(r.get('custom_llm_provider')=='github_copilot' for r in rows),
        'all_requested_model':all(r.get('model')==inv['routes'][0]['litellm_params']['model'] for r in rows),
        'actual_api_hosts_are_githubcopilot':all((urlsplit(r.get('api_base') or '').hostname or '').endswith('.githubcopilot.com') for r in rows),
        'no_logged_retries':all(m.get('attempted_retries') in (None,0) for m in metadata),
        'no_logged_cache_hits':all(str(r.get('cache_hit')).lower() in ('none','false','0','') for r in rows),
        'local_alternate_target_requests_since_fixture_start':counts().get('alternate',0)}
    for name,value in evidence.items():
        if name.startswith(('all_','actual_','no_')):assert value,name+' failed'
    assert evidence['local_alternate_target_requests_since_fixture_start']==0,'Alternate target was invoked; inspect the private ledger'
    private_write(state/'routing-evidence-source.json',rows)
    private_write(state/'routing-evidence-summary.json',evidence)
    print(json.dumps(evidence,indent=2))
if __name__=='__main__':main()
