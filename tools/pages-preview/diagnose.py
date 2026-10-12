"""Bounded GET-only diagnostic. No publisher import, subprocess, or mutation path."""
import json
import os
import re
import sys
import urllib.error
import urllib.request

PROJECT = 'roy-web-sales-previews'
REPOSITORY = 'roy-baty-ai/roy-preview-publisher'
BRANCH = 'reamertec'
CONTENT_SHA = '3ab1a526eb2ea6e91f1ab6697649c9adc1b5a214'
MARKER = 'roy-preview-38250609905-1'
MAX_BODY = 1024 * 1024
STAGES = (
    ('project', ''),
    ('production_deployments', '/deployments?env=production&per_page=100&page=1'),
    ('preview_deployments', '/deployments?env=preview&per_page=100&page=1'),
)

class Stop(Exception):
    pass

class RedirectStopped(Stop):
    def __init__(self, status):
        self.status = status

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if response is not None:
            response.close()
        raise RedirectStopped(code)  # Never transmit credentials to a redirect destination.

def emit(stage, status=None, codes=None, valid=None, **state):
    print(json.dumps({'stage': stage, 'http_status': status,
                      'cloudflare_error_codes': codes or [], 'json_valid': valid, **state}, sort_keys=True))

def decode(raw):
    if len(raw) > MAX_BODY:
        return None, False
    try:
        return json.loads(raw), True
    except (ValueError, UnicodeError):
        return None, False

def error_codes(data):
    errors = data.get('errors') if isinstance(data, dict) else None
    if not isinstance(errors, list):
        return []
    return [e['code'] for e in errors if isinstance(e, dict)
            and type(e.get('code')) is int and 0 <= e['code'] <= 9999999][:32]

def get(opener, base, token, stage, suffix):
    status, data, valid = None, None, None
    try:
        request = urllib.request.Request(base + suffix, method='GET',
            headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
        with opener.open(request, timeout=15) as response:
            status = response.status
            data, valid = decode(response.read(MAX_BODY + 1))
    except RedirectStopped as error:
        status = error.status
    except urllib.error.HTTPError as error:
        status = error.code
        # Never follow/read a redirect destination. The status itself is sufficient.
        try:
            if not 300 <= status < 400:
                data, valid = decode(error.read(MAX_BODY + 1))
        except Exception:
            pass
        finally:
            error.close()
    except Exception:
        pass  # Do not print repr, reason, URL, headers, or exception message.
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    emit(stage, status, error_codes(data), valid)
    if status != 200 or not isinstance(data, dict) or data.get('success') is not True or data.get('errors') or 'result' not in data:
        raise Stop()
    return data['result']

def run(account, token, opener=None):
    if not re.fullmatch('[0-9a-f]{32}', account) or not token or '\n' in token or '\r' in token:
        emit('input_rejected')
        return 1
    # Direct fixed Cloudflare host only; no proxy environment inheritance.
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    base = f'https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects/{PROJECT}'
    try:
        project = get(opener, base, token, *STAGES[0])
        if not isinstance(project, dict) or project.get('name') != PROJECT or project.get('subdomain') != PROJECT + '.pages.dev':
            raise Stop()
        prod_branch = project.get('production_branch')
        canonical = project.get('canonical_deployment', 'missing')
        configs = project.get('deployment_configs')
        emit('production_state',
             production_branch_relation=('unknown' if not isinstance(prod_branch, str) or not prod_branch.strip()
                                         else 'target' if prod_branch.casefold() == BRANCH else 'other'),
             canonical=('none' if canonical is None else 'present' if isinstance(canonical, dict) and isinstance(canonical.get('id'), str)
                        else 'unknown'),
             production_config=('object' if isinstance(configs, dict) and isinstance(configs.get('production'), dict) else 'unknown'))
        production = get(opener, base, token, *STAGES[1])
        if not isinstance(production, list) or not all(isinstance(d, dict) and d.get('environment') == 'production' for d in production):
            raise Stop()
        emit('production_history', production=('none_returned' if not production else 'present'))
        preview = get(opener, base, token, *STAGES[2])
        if not isinstance(preview, list) or not all(isinstance(d, dict) and d.get('environment') == 'preview' for d in preview):
            raise Stop()
        matches = []
        for d in preview:
            trigger = d.get('deployment_trigger')
            meta = trigger.get('metadata') if isinstance(trigger, dict) else None
            if isinstance(meta, dict) and meta.get('commit_message') == MARKER:
                matches.append((d, meta))
        state = 'not_seen_in_first_100'
        if len(matches) > 1:
            state = 'ambiguous'
        elif matches:
            d, meta = matches[0]
            if meta.get('branch') != BRANCH or meta.get('commit_hash') != CONTENT_SHA:
                state = 'metadata_mismatch'
            else:
                latest = d.get('latest_stage')
                state = 'found_success' if isinstance(latest, dict) and latest.get('name') == 'deploy' and latest.get('status') == 'success' else 'found_not_confirmed_success'
        emit('expected_marker', marker=MARKER, marker_state=state)
        return 0
    except Exception:
        emit('diagnostic_stopped')
        return 1

def main():
    # Environment values are injected only by the future protected runtime job.
    if (os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or
        os.environ.get('GITHUB_REF') != 'refs/heads/main' or
        os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'):
        emit('context_rejected')
        return 1
    try:
        return run(os.environ.get('CLOUDFLARE_ACCOUNT_ID', ''), os.environ.get('CLOUDFLARE_API_TOKEN', ''))
    except Exception:
        emit('diagnostic_stopped')
        return 1

if __name__ == '__main__':
    sys.exit(main())
