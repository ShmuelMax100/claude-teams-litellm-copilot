"""Local synthetic provider; counts requests without storing headers or prompts."""
import json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
counts={};lock=threading.Lock()
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,status,body):
        data=json.dumps(body).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        with lock:self.reply(200,dict(counts))
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))));model=body.get('model','unknown')
        with lock:counts[model]=counts.get(model,0)+1
        if model=='stream-error' or (body.get('stream') and model=='alternate'):
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            event={'error':{'message':'Synthetic stream failure','type':'server_error','code':500}} if model=='stream-error' else {'id':'local-stream','object':'chat.completion.chunk','model':model,'choices':[{'index':0,'delta':{'role':'assistant','content':'LOCAL_OK'},'finish_reason':None}]}
            self.wfile.write(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode());self.wfile.flush();return
        if model in ('generic','context','content'):
            code,message={'generic':(500,'Synthetic provider failure'),'context':(400,'This model\'s maximum context length is 16 tokens. Please reduce the length of the messages.'),'content':(400,'Your request was rejected as a result of our safety system. content_policy_violation')}[model]
            self.reply(code,{'error':{'message':message,'type':'invalid_request_error','code':'content_policy_violation' if model=='content' else 'context_length_exceeded' if model=='context' else 'server_error'}})
        else:self.reply(200,{'id':'synthetic-local','object':'chat.completion','model':model,'choices':[{'index':0,'message':{'role':'assistant','content':'LOCAL_OK'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
ThreadingHTTPServer(('0.0.0.0',8080),Handler).serve_forever()
