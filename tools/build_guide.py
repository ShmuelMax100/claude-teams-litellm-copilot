#!/usr/bin/env python3
"""Build a standalone guide for the sole Fusion gateway architecture."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
commands = [
    {'id': 'prepare', 'command': './setup admin prepare --gateway https://gateway.example --upstream https://fusion.internal.example/v1', 'context': 'Administrator: verify the deployed gateway'},
    {'id': 'enroll', 'command': './setup admin enroll --gateway https://gateway.example --upstream https://fusion.internal.example/v1 --user-id developer-id --github-login developer-login --tier standard --out private/developer.invite.json --apply', 'context': 'Administrator: issue a personal invitation'},
    {'id': 'join', 'command': './setup developer join --invite /path/to/personal-invitation.json', 'context': 'Developer: connect Claude Code'},
    {'id': 'doctor', 'command': './setup doctor', 'context': 'Developer: check settings and authorization'},
    {'id': 'models', 'command': './setup models', 'context': 'List the Fusion tiers'},
]

def command(index):
    item = commands[index]
    return '<p>' + html.escape(item['context']) + '</p><pre id="' + item['id'] + '">' + html.escape(item['command']) + '</pre>'

svg = (ROOT / 'diagrams/architecture.svg').read_text()
page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Fusion gateway setup</title>
<style>
:root{font:17px/1.6 system-ui,sans-serif;color:#19324a;background:#f3f6fa}body{max-width:980px;margin:auto;padding:32px 24px 72px}header,section{background:white;border:1px solid #d5e0e9;border-radius:18px;padding:28px;margin-bottom:22px}h1{font-size:42px;line-height:1.15;margin:0 0 18px}h2{font-size:26px;margin-top:0}p{max-width:80ch}nav{display:flex;flex-wrap:wrap;gap:18px;margin:24px 0}a{color:#145e9e}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf3f8;border-radius:10px;padding:18px;font:14px/1.6 ui-monospace,monospace}svg{width:100%;height:auto}button{font:inherit;padding:10px 20px;border:1px solid #93aec4;border-radius:10px;background:white;color:#19324a;cursor:pointer;margin-right:12px}button[aria-pressed=true]{background:#145e9e;color:white}li{margin:8px 0}.note{border-left:4px solid #b27e20;padding-left:18px}code{font-size:.9em}footer{font-size:14px;color:#50677a}@media(max-width:600px){body{padding:16px 12px}header,section{padding:20px}h1{font-size:32px}}@media print{section[hidden]{display:block}button{display:none}}
</style></head><body><header><p>CLAUDE CODE · AZURE CONTAINER APPS</p><h1>One gateway.<br>Fusion from the first request.</h1><p>LiteLLM and Fusion run inside one ACA environment. Each developer has a separate identity. Standard, High and Max select Fusion tiers; GitHub controls the internal models.</p>''' + svg + '''<p>No Claude subscription route or alternative provider is configured. Service failures return clear errors.</p><div aria-label="Setup role"><button data-role="admin" aria-pressed="false">Administrator</button><button data-role="developer" aria-pressed="false">Developer</button></div></header>
<nav><a href="#architecture">How it works</a><a href="#limits">Compatibility</a><a href="#verification">Verification</a></nav>
<section id="admin" data-journey="admin" hidden><h2>Deploy, verify, then invite</h2><ol><li>Build the pinned Fusion image and review the Terraform examples in <code>infra/terraform</code>.</li><li>Store a distinct GitHub fine-grained PAT for each user in Key Vault, with Copilot Requests permission and the real expiry.</li><li>Deploy both applications in one ACA environment, with Fusion private and only LiteLLM public. Follow <code>docs/AZURE-DEPLOYMENT.md</code>.</li><li>Create the matching LiteLLM user ID with role <code>internal_user</code>, administrator-managed <code>metadata.github_login</code>, and automatic key generation disabled. The exact management API body is in <code>docs/ADMINISTRATOR.md</code>. Verify the deployed configuration.</li></ol>''' + command(0) + command(1) + '''<p>The invitation contains a personal gateway key. Transfer it privately. Never include GitHub credentials. Read <code>docs/ADMINISTRATOR.md</code> before provisioning.</p></section>
<section id="developer" data-journey="developer" hidden><h2>Connect your Claude Code</h2><p>Use your administrator's personal invitation. Join verifies it and backs up your settings before writing the gateway configuration. GitHub credentials remain on the server.</p>''' + command(2) + command(3) + command(4) + '''<p>Then start <code>claude</code>. If doctor finds inherited provider variables, clear them and start a new terminal. Use <code>./setup developer restore</code> to restore the saved settings. No Claude subscription login is needed for this configuration.</p></section>
<section id="architecture"><h2>Model requests and tools have different interfaces</h2><p>Claude Code sends Anthropic Messages to LiteLLM. The gateway authenticates the personal key, translates the request to Chat Completions and signs the user's identity. Fusion resolves that user's server-side credential and creates an isolated SDK session.</p><p>Text streams back while Fusion runs. Complete validated tool calls return to Claude Code for local execution. MCP is a separate private tool endpoint; it is not the model adapter and is not registered with a shared user token.</p><p>All three model names—<code>fusion-standard</code>, <code>fusion-high</code> and <code>fusion-max</code>—use this same route. The key grants its selected tier.</p></section>
<section id="limits"><h2>Know the compatibility boundary</h2><p class="note">Anthropic does not support routing Claude Code to non-Claude models. This adapter implements a tested text-and-tools subset, not full Claude feature compatibility.</p><p>The stable SDK accepts prior conversation as serialized context, not lossless native Claude history. Images, audio, advanced thinking, forced tool selection and unsupported options fail explicitly. The returned-output byte cap does not limit upstream generation charges.</p><p>Warm containers reduce startup delay. Network and model generation still add latency. A failure never switches provider; restarting a request may repeat work.</p></section>
<section id="verification"><h2>Check the deployment in layers</h2><p>Setup and doctor check authorization and configuration without model inference. Automated tests cover request translation, streaming, tool calls, two-user isolation, service failures, expired credentials and the absence of alternative routing.</p><p>Native offline SDK probes verify runtime and tool interfaces. They do not establish live account entitlement. A real Fusion request requires the user's own approved credential. Read <code>docs/VALIDATION.md</code> and <code>docs/IMPLEMENTATION-RESULTS.md</code> for current evidence.</p></section>
<footer>Source documentation is authoritative. This guide is generated from repository sources and uses no external assets. Updated 22 November 2026.</footer>
<script>
document.querySelectorAll('[data-role]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-role]').forEach(other => other.setAttribute('aria-pressed', String(other === button)));
  document.querySelectorAll('[data-journey]').forEach(section => { section.hidden = section.dataset.journey !== button.dataset.role; });
  document.getElementById(button.dataset.role).scrollIntoView({behavior: 'smooth'});
}));
</script></body></html>'''
(ROOT / 'START-HERE.html').write_text(page)
(ROOT / 'tools/guide-commands.json').write_text(json.dumps(commands, indent=2) + '\n')
print('Built standalone Fusion gateway guide and command reference.')
