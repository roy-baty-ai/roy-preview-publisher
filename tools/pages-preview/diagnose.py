"""Bounded GET-only diagnostic. No publisher import, subprocess, or mutation path."""
import json
import os
import re
import sys
import urllib.error
import urllib.request

PROJECT = 'roy-web-sales-previews'
REPOSITORY = 'roy-baty-ai/roy-preview-publisher'
BRANCH = 'window-door-demo'
CONTENT_SHA = '2fc8dbbb62477d5cb44fd967495090ad77107941'
MARKER = 'roy-preview-38377559460-1'
MAX_BODY = 1024 * 1024
PER_PAGE = 10
MAX_PAGES = 5  # At most 50 deployments per environment; never infer absence at this limit.
ALIAS = f'https://{BRANCH}.{PROJECT}.pages.dev'

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

def deployments(opener, base, token, environment):
    found, ids = [], set()
    for page in range(1, MAX_PAGES + 1):
        batch = get(opener, base, token, environment + '_deployments',
                    f'/deployments?env={environment}&per_page={PER_PAGE}&page={page}')
        if not isinstance(batch, list) or len(batch) > PER_PAGE:
            raise Stop()
        for item in batch:
            if (not isinstance(item, dict) or item.get('environment') != environment or
                not isinstance(item.get('id'), str) or
                not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', item['id']) or
                item['id'] in ids):
                raise Stop()
            ids.add(item['id'])
        found.extend(batch)
        if len(batch) < PER_PAGE:
            return found
    emit(environment + '_pagination_limit', scan_complete=False)
    raise Stop()

def run(account, token, opener=None):
    if not re.fullmatch('[0-9a-f]{32}', account) or not token or '\n' in token or '\r' in token:
        emit('input_rejected')
        return 1
    # Direct fixed Cloudflare host only; no proxy environment inheritance.
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    base = f'https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects/{PROJECT}'
    try:
        project = get(opener, base, token, 'project', '')
        if (not isinstance(project, dict) or project.get('name') != PROJECT or
            project.get('subdomain') != PROJECT + '.pages.dev' or project.get('source')):
            raise Stop()
        prod_branch = project.get('production_branch')
        canonical = project.get('canonical_deployment', 'missing')
        configs = project.get('deployment_configs')
        if (not isinstance(prod_branch, str) or not prod_branch.strip() or prod_branch != prod_branch.strip() or
            prod_branch.casefold() == BRANCH or
            not isinstance(configs, dict) or not isinstance(configs.get('production'), dict) or
            (canonical is not None and (not isinstance(canonical, dict) or
                canonical.get('environment') != 'production' or not isinstance(canonical.get('id'), str)))):
            raise Stop()
        production = deployments(opener, base, token, 'production')
        canonical_consistent = canonical is None or any(x['id'] == canonical['id'] for x in production)
        production_empty = canonical is None and not production
        emit('production_state', production_branch_relation='other',
             canonical='none' if canonical is None else 'present', production_config='object',
             production_count=len(production), canonical_consistent=canonical_consistent,
             current_production_empty=production_empty,
             historical_production_unchanged='not_assessed')
        preview = deployments(opener, base, token, 'preview')
        matches = []
        for item in preview:
            trigger = item.get('deployment_trigger')
            meta = trigger.get('metadata') if isinstance(trigger, dict) else None
            if not isinstance(meta, dict):
                raise Stop()  # Cannot establish marker uniqueness from malformed metadata.
            if meta.get('commit_message') == MARKER:
                matches.append((item, meta))
        state, urls = 'not_found', {}
        if len(matches) > 1:
            state = 'ambiguous'
        elif matches:
            item, meta = matches[0]
            latest = item.get('latest_stage')
            if meta.get('branch') != BRANCH or meta.get('commit_hash') != CONTENT_SHA:
                state = 'metadata_mismatch'
            elif not isinstance(latest, dict):
                state = 'unknown_stage'
            elif latest.get('status') in ('failure', 'canceled'):
                state = 'deploy_failed'
            elif latest.get('name') != 'deploy' or latest.get('status') != 'success':
                state = 'pending'
            elif not isinstance(item.get('url'), str) or not re.fullmatch(
                    r'https://[0-9a-f]{8,32}\.roy-web-sales-previews\.pages\.dev', item['url']):
                state = 'url_rejected'
            elif not isinstance(item.get('aliases'), list) or ALIAS not in item['aliases']:
                state = 'alias_missing_or_wrong'
            else:
                state = 'api_verified'
                urls = {'deployment_url': item['url'], 'alias_url': ALIAS}
        emit('expected_marker', marker=MARKER, branch=BRANCH, commit=CONTENT_SHA,
             marker_state=state, scan_complete=True, public_content_verified=False, **urls)
        # API confirmation does not prove that either public URL serves the expected bytes.
        return 0 if state == 'api_verified' and canonical_consistent and production_empty else 1
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
