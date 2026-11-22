import asyncio
from dataclasses import replace
from types import SimpleNamespace
import pytest
from copilot_runner import CopilotRunner
from inference_types import CompletionRequest, ToolDefinition
from conftest import FakeSession


def request(**changes):
    return CompletionRequest(**{'tier': 'high', 'prompt': '[{"role":"user","content":"Read @notes.md"}]',
        'system_message': 'Continue using the supplied history as context.',
        'tools': (ToolDefinition('Read', 'Read a local file', {'type': 'object'}),
                  ToolDefinition('Bash', 'Run a local command', {'type': 'object'})), **changes})


async def test_completion_direct_and_complete_parallel_tool_batch(config, factory, monkeypatch):
    runner = CopilotRunner(config, client_factory=factory)
    await runner.probe()
    async def send(session, prompt):
        assert '@notes.md' in prompt
        tools = session.options['tools']
        assert tools['available_tools'] == ['custom:fusion_caller_0', 'custom:fusion_caller_1']
        assert all(tool.handler is None for tool in tools['definitions'])
        assert [tool.name for tool in tools['definitions']] == ['fusion_caller_0', 'fusion_caller_1']
        session.emit('assistant.usage', input_tokens=27, output_tokens=13)
        session.emit('assistant.message', content='Inspecting the supplied files.', tool_requests=[
            SimpleNamespace(name='fusion_caller_0', tool_call_id='call-1', arguments={'path': 'notes.md'}),
            SimpleNamespace(name='fusion_caller_1', tool_call_id='call-2', arguments={'command': 'pwd'})])
        # A later pending request cannot reduce the captured batch to one item.
        session.emit('external_tool.requested', tool_name='fusion_caller_0', request_id='pending')
        return 'message-1'
    monkeypatch.setattr(FakeSession, 'send', send)
    result = await runner.complete(request())
    assert result.ok and result.finish_reason == 'tool_calls'
    assert [(t.id, t.name) for t in result.tool_calls] == [('call-1', 'Read'), ('call-2', 'Bash')]
    assert result.usage == {'input_tokens': 27, 'output_tokens': 13}
    assert factory.clients[0].sessions[0].aborted
    assert factory.clients[0].sessions[0].disconnected
    assert factory.clients[0].deleted == [result.request_id]
    await runner.close()


@pytest.mark.parametrize('requests', [
    [SimpleNamespace(name='shell', tool_call_id='one', arguments={})],
    [SimpleNamespace(name='fusion_caller_0', tool_call_id='one', arguments='not-a-dict')],
    [SimpleNamespace(name='fusion_caller_0', tool_call_id='one', arguments={}),
     SimpleNamespace(name='fusion_caller_1', tool_call_id='one', arguments={})],
])
async def test_invalid_tool_batch_is_rejected(config, factory, monkeypatch, requests):
    runner = CopilotRunner(config, client_factory=factory)
    await runner.probe()
    async def send(session, prompt):
        session.emit('assistant.message', content='', tool_requests=requests)
    monkeypatch.setattr(FakeSession, 'send', send)
    result = await runner.complete(request())
    assert not result.ok and result.status == 'tool_denied' and result.tool_calls == []
    await runner.close()


async def test_context_bounds_are_separate_from_mcp_prompt_syntax(config, factory):
    runner = CopilotRunner(replace(config, max_input_chars=4), client_factory=factory)
    await runner.probe()
    result = await runner.complete(request(tools=()))
    assert result.ok and result.usage is None
    assert (await runner.run('high', 'long prompt')).status == 'invalid_input'
    assert (await runner.complete(request(prompt='x' * 262145))).status == 'invalid_input'
    await runner.close()


@pytest.mark.parametrize('where', ['arguments', 'id', 'name'])
async def test_tool_credential_echo_is_rejected(config, factory, monkeypatch, where):
    runner = CopilotRunner(config, client_factory=factory)
    await runner.probe()
    async def send(session, prompt):
        session.emit('assistant.message', content='', tool_requests=[SimpleNamespace(
            name='fusion_caller_0',
            tool_call_id=config.credentials["owner-user-id"].token if where == 'id' else 'call-1',
            arguments={'text': config.credentials["owner-user-id"].token} if where == 'arguments' else {})])
    monkeypatch.setattr(FakeSession, 'send', send)
    tool = ToolDefinition(config.credentials["owner-user-id"].token if where == 'name' else 'Read', 'Caller tool', {'type': 'object'})
    result = await runner.complete(request(tools=(tool,)))
    assert not result.ok and result.status == 'provider_error' and not result.tool_calls
    assert config.credentials["owner-user-id"].token not in result.model_dump_json()
    await runner.close()


async def test_output_limit_counts_complete_tool_payload(config, factory, monkeypatch):
    runner = CopilotRunner(replace(config, max_output_bytes=128), client_factory=factory)
    await runner.probe()
    async def send(session, prompt):
        session.emit('assistant.message', content='', tool_requests=[SimpleNamespace(
            name='fusion_caller_0', tool_call_id='call-1', arguments={'text': 'x' * 128})])
    monkeypatch.setattr(FakeSession, 'send', send)
    result = await runner.complete(request())
    assert not result.ok and result.status == 'output_limit' and not result.tool_calls
    await runner.close()
