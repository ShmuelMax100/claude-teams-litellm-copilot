#!/usr/bin/env python3
"""Preview a fixed synthetic batch; execute only after explicit user approval."""
import argparse,json,sys,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'_engine'))
from gateway import Error,private_write,read
import copilot
DIRECT='Reply with exactly COPILOT_PILOT_OK.'
STREAM='Reply with exactly COPILOT_STREAM_OK.'
TOOL='Call echo with value COPILOT_TOOL_OK, then reply with exactly COPILOT_TOOL_OK after reading its result.'

def main():
    p=argparse.ArgumentParser();p.add_argument('--invite',type=Path,required=True);p.add_argument('--approved',action='store_true');p.add_argument('--resume',action='store_true');a=p.parse_args()
    inv=read(a.invite);model=inv['models'][0]
    plan={'destination':'GitHub Copilot through the isolated local LiteLLM gateway','alias':model,
        'provider_model':inv['routes'][0]['litellm_params']['model'],'prompts':[DIRECT,STREAM,TOOL],
        'synthetic_tool_result':'COPILOT_TOOL_OK','planned_inference_requests':4,'total_request_cap':10}
    print(json.dumps(plan,indent=2))
    if not a.approved:return
    if inv['protocol']!='chat':raise Error('This acceptance batch uses Chat Completions.')
    api=copilot.validate(inv);copilot.doctor(inv)
    if api.url!='http://127.0.0.1:4198':raise Error('Live batch is restricted to the isolated pilot gateway.')
    counter=ROOT/'private/pilot/live-request-count.json'
    results_path=ROOT/'private/pilot/live-results.json'
    results=read(results_path) if a.resume and results_path.exists() else {}
    ids_path=ROOT/'private/pilot/live-request-ids.json'
    def record_id(value):
        if not value:return
        ids=read(ids_path) if ids_path.exists() else []
        if value not in ids:ids.append(value);private_write(ids_path,ids)
    tool={'type':'function','function':{'name':'echo','description':'Return the supplied synthetic value unchanged.',
        'parameters':{'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}}}
    def request(messages,stream=False,**extra):
        n=read(counter)['requests'] if counter.exists() else 0
        if n>=10:raise Error('The approved ten-request cap is exhausted.')
        private_write(counter,{'requests':n+1}) # Count failures too; never retry implicitly.
        body={'model':model,'messages':messages,'max_tokens':256,'stream':stream,'max_retries':0,'num_retries':0,**extra}
        if not stream:
            response=api.call('/v1/chat/completions','POST',body);record_id(response.get('id'));return response
        req=urllib.request.Request(api.url+'/v1/chat/completions',data=json.dumps(body).encode(),
            headers={'Authorization':'Bearer '+api.key,'Content-Type':'application/json'})
        chunks=[]
        with api.opener.open(req,timeout=90) as response:
            for line in response:
                if not line.startswith(b'data: '):continue
                value=line[6:].strip()
                if value==b'[DONE]':break
                event=json.loads(value)
                record_id(event.get('id'))
                if 'error' in event:raise Error('Live stream returned an error; private provider payload was not printed.')
                for choice in event.get('choices',[]):chunks.append(choice.get('delta',{}).get('content') or '')
        return ''.join(chunks)
    def user(prompt):return [{'role':'user','content':prompt}]
    if results.get('direct')!='passed':
        r=request(user(DIRECT));assert 'COPILOT_PILOT_OK' in r['choices'][0]['message']['content']
        results['direct']='passed';private_write(results_path,results)
    if results.get('stream')!='passed':
        assert 'COPILOT_STREAM_OK' in request(user(STREAM),stream=True)
        results['stream']='passed';private_write(results_path,results)
    if results.get('synthetic_tool_round_trip')=='passed':
        print(json.dumps(results,indent=2));return
    first=request(user(TOOL),tools=[tool])
    message=first['choices'][0]['message'];calls=message.get('tool_calls') or []
    assert len(calls)==1 and calls[0]['function']['name']=='echo'
    assert json.loads(calls[0]['function']['arguments'])=={'value':'COPILOT_TOOL_OK'}
    final=request(user(TOOL)+[message,{'role':'tool','tool_call_id':calls[0]['id'],'content':'COPILOT_TOOL_OK'}],tools=[tool])
    assert 'COPILOT_TOOL_OK' in final['choices'][0]['message']['content'];results['synthetic_tool_round_trip']='passed'
    results['scope']='One synthetic tool only; not full tool parity. Provider routing needs gateway evidence.'
    private_write(ROOT/'private/pilot/live-results.json',results);print(json.dumps(results,indent=2))
if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Live batch did not complete ('+type(error).__name__+'). No provider response, key or private telemetry was printed.',file=sys.stderr);sys.exit(1)
