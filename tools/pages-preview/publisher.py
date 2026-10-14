"""Trusted static-only uploader. No site code is executed. CLI fails closed."""
import contextlib
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PROJECT = 'roy-web-sales-previews'
REPOSITORY = 'roy-baty-ai/roy-preview-publisher'
BRANCHES = ('glass-green', 'reamertec', 'window-door-demo')
CONTENT_REF = 'refs/remotes/origin/preview-content'
# Official Pages OpenAPI uses per_page=10 as its example; no maximum is specified.
DEPLOYMENTS_PER_PAGE = 10
SUFFIXES = {'.html', '.css', '.js', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.ico', '.woff', '.woff2', '.txt'}
MAX_FILE = 25 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
MAX_FILES = 2000

class Refused(Exception):
    pass

def require(ok, code):
    if not ok:
        raise Refused(code)

def branch_ok(branch):
    require(branch in BRANCHES, 'BRANCH_REJECTED')

def sha_ok(sha):
    require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha), 'SHA_REJECTED')

def file_ok(name):
    p = PurePosixPath(name)
    require(name and not p.is_absolute() and str(p) == name, 'PATH_REJECTED')
    require(all(re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', x) and x not in ('.', '..') for x in p.parts), 'PATH_REJECTED')
    require(not any(x.lower() in ('functions', 'node_modules') for x in p.parts), 'CODE_REJECTED')
    require(p.suffix.lower() in SUFFIXES, 'TYPE_REJECTED')

# No inherited secret variables or Git configuration injection into subprocesses.
def git(repo, *args):
    env = {'PATH': os.defpath, 'HOME': '/nonexistent', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
    r = subprocess.run(['git', '-C', str(repo), *args], env=env, capture_output=True, timeout=60)
    require(r.returncode == 0, 'GIT_REJECTED')
    return r.stdout

def prepare(repo, stage, branch, sha):
    branch_ok(branch)
    sha_ok(sha)
    git(repo, 'merge-base', '--is-ancestor', sha, CONTENT_REF)
    prefix = f'preview-assets/{branch}/'
    rows = git(repo, 'ls-tree', '-r', '-l', '-z', sha, '--', prefix).split(b'\0')
    blobs = []
    total = 0
    for row in filter(None, rows):
        metadata, raw_name = row.split(b'\t', 1)
        mode, kind, oid, size = metadata.split()
        require(mode == b'100644' and kind == b'blob', 'NON_REGULAR_ASSET')
        name = raw_name.decode('utf-8')
        require(name.startswith(prefix), 'PATH_REJECTED')
        relative = name[len(prefix):]
        file_ok(relative)
        size = int(size)
        total += size
        require(size <= MAX_FILE and total <= MAX_TOTAL, 'ASSET_TOO_LARGE')
        blobs.append((relative, oid.decode(), size))
        require(len(blobs) <= MAX_FILES, 'TOO_MANY_ASSETS')
    require(any(x[0] == 'index.html' and x[2] > 0 for x in blobs), 'INDEX_REQUIRED')
    require(not stage.exists(), 'STAGE_EXISTS')
    assets = stage / 'assets'
    assets.mkdir(parents=True, mode=0o700)
    hashes = {}
    for name, oid, size in blobs:
        data = git(repo, 'cat-file', 'blob', oid)
        require(len(data) == size, 'ASSET_CHANGED')
        dest = assets / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    # Trusted headers, never user-supplied _headers / _redirects / _worker.js.
    headers = b'/*\n  X-Robots-Tag: noindex, nofollow, noarchive\n'
    (assets / '_headers').write_bytes(headers)
    hashes['_headers'] = hashlib.sha256(headers).hexdigest()
    manifest = {'branch': branch, 'commit': sha, 'files': hashes}
    (stage / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    return manifest

def verify_stage(stage, branch, sha):
    branch_ok(branch)
    sha_ok(sha)
    require(not stage.is_symlink() and not (stage / 'assets').is_symlink(), 'SYMLINK_REJECTED')
    require(not (stage / 'manifest.json').is_symlink(), 'SYMLINK_REJECTED')
    m = json.loads((stage / 'manifest.json').read_text())
    require(m['branch'] == branch and m['commit'] == sha, 'MANIFEST_MISMATCH')
    files = {}
    total = 0
    for p in (stage / 'assets').rglob('*'):
        require(not p.is_symlink(), 'SYMLINK_REJECTED')
        if p.is_dir():
            continue
        require(p.is_file(), 'NON_REGULAR_ASSET')
        name = p.relative_to(stage / 'assets').as_posix()
        if name != '_headers':
            file_ok(name)
        require(p.stat().st_size <= MAX_FILE, 'ASSET_TOO_LARGE')
        data = p.read_bytes()
        total += len(data)
        require(total <= MAX_TOTAL, 'ASSET_TOO_LARGE')
        files[name] = hashlib.sha256(data).hexdigest()
        require(len(files) <= MAX_FILES + 1, 'TOO_MANY_ASSETS')
    require(files == m['files'] and 'index.html' in files, 'ASSET_CHANGED')
    require((stage / 'assets' / '_headers').read_bytes() == b'/*\n  X-Robots-Tag: noindex, nofollow, noarchive\n', 'HEADERS_CHANGED')
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()

class RedirectRejected(Refused):
    def __init__(self, status):
        super().__init__('REDIRECT_REJECTED')
        self.status = status

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if response is not None:
            response.close()
        raise RedirectRejected(code)

class API:
    def __init__(self, account, token):
        require(re.fullmatch('[0-9a-f]{32}', account), 'ACCOUNT_REJECTED')
        require(token and '\n' not in token and '\r' not in token, 'TOKEN_REQUIRED')
        self.base = f'https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects/{PROJECT}'
        self.token = token

    def get(self, suffix=''):
        try:
            req = urllib.request.Request(self.base + suffix, headers={'Authorization': 'Bearer ' + self.token})
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open(req, timeout=30) as r:
                require(r.status == 200, 'API_FAILED')
                data = json.loads(r.read(8 * 1024 * 1024))
            require(data.get('success') is True and not data.get('errors'), 'API_FAILED')
            return data['result']
        except Exception:
            # Never log raw errors/responses/headers (project payload may include secrets).
            raise Refused('API_FAILED') from None

    def deployments(self, env):
        result = []
        for page in range(1, 101):
            batch = self.get(f'/deployments?env={env}&per_page={DEPLOYMENTS_PER_PAGE}&page={page}')
            require(isinstance(batch, list), 'API_SCHEMA_REJECTED')
            require(all(d['environment'] == env for d in batch), 'API_SCHEMA_REJECTED')
            result.extend(batch)
            if len(batch) < DEPLOYMENTS_PER_PAGE:
                return result
        raise Refused('PAGINATION_LIMIT')

def projection(d):
    if d is None:
        return None
    require(isinstance(d.get('id'), str) and d['id'], 'API_SCHEMA_REJECTED')
    return {k: d.get(k) for k in ('id', 'environment', 'url', 'aliases', 'deployment_trigger', 'latest_stage')}

def snapshot(api, branch):
    p = api.get()
    require(p.get('name') == PROJECT and p.get('subdomain') == PROJECT + '.pages.dev', 'PROJECT_REJECTED')
    require(not p.get('source'), 'NOT_DIRECT_UPLOAD')
    prod = p.get('production_branch')
    require(isinstance(prod, str) and prod.strip() and prod == prod.strip(), 'PRODUCTION_BRANCH_UNKNOWN')
    require(branch.casefold() != prod.casefold(), 'PRODUCTION_REJECTED')
    require('canonical_deployment' in p, 'API_SCHEMA_REJECTED')
    canonical = projection(p['canonical_deployment'])
    require(canonical is None or canonical['environment'] == 'production', 'API_SCHEMA_REJECTED')
    configs = p.get('deployment_configs')
    require(isinstance(configs, dict) and isinstance(configs.get('production'), dict), 'PRODUCTION_CONFIG_UNKNOWN')
    deployments = sorted((projection(d) for d in api.deployments('production')), key=lambda d: d['id'])
    return {'branch': prod, 'canonical': canonical, 'deployments': deployments,
            'config': configs['production']}

@contextlib.contextmanager
def exclusive(lock):
    with open(lock, 'a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('CONCURRENT_RUN') from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def valid_url(url):
    return isinstance(url, str) and re.fullmatch(r'https://[0-9a-f]{8,32}\.roy-web-sales-previews\.pages\.dev', url)

def probe_result(role, state, status=None, codes=None, hash_match=None):
    require(role in ('deployment', 'alias'), 'PROBE_ROLE_REJECTED')
    return {'role': role, 'state': state, 'http_status': status,
            'cloudflare_error_codes': codes or [], 'hash_match': hash_match}

def public_probe(url, expected, role='deployment'):
    # Same request headers as before. No access-control bypass or credentials.
    require(role in ('deployment', 'alias'), 'PROBE_ROLE_REJECTED')
    status = None
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
        request = urllib.request.Request(url + '/', headers={'Accept-Encoding': 'identity'})
        with opener.open(request, timeout=30) as response:
            status = response.status
            if status != 200:
                return probe_result(role, 'access_denied' if status in (401, 403) else
                                    'redirect_rejected' if 300 <= status < 400 else 'http_error', status)
            raw = response.read(MAX_FILE + 1)
            match = len(raw) <= MAX_FILE and hashlib.sha256(raw).hexdigest() == expected
            return probe_result(role, 'verified' if match else 'content_mismatch', status, hash_match=match)
    except RedirectRejected as error:
        return probe_result(role, 'redirect_rejected', error.status)
    except urllib.error.HTTPError as error:
        status, codes = error.code, []
        try:
            # Recognize only the known denial code; never emit arbitrary body text.
            if status == 403:
                raw = error.read(64 * 1024)
                if re.search(rb'(?i)\b(?:error(?:\s+code)?\s*:?\s*|code["\']?\s*:\s*)1010\b', raw):
                    codes = [1010]
        except Exception:
            pass
        finally:
            error.close()
        return probe_result(role, 'access_denied' if status in (401, 403) else
                            'redirect_rejected' if 300 <= status < 400 else 'http_error', status, codes)
    except Exception:
        return probe_result(role, 'network_failure', status)

def publish(api, deploy, stage, branch, sha, marker, lock, pause=time.sleep, probe=public_probe):
    branch_ok(branch)
    sha_ok(sha)
    require(re.fullmatch(r'roy-preview-[0-9]+-[0-9]+', marker), 'RUN_REJECTED')
    with exclusive(lock):
        digest = verify_stage(stage, branch, sha)
        before = snapshot(api, branch)
        require(before['canonical'] is None and not before['deployments'], 'UNEXPECTED_EXISTING_PRODUCTION')
        old_ids = {d['id'] for d in api.deployments('preview')}
        # Re-read immediately before Wrangler; fail closed on change during preparation.
        require(snapshot(api, branch) == before, 'PRODUCTION_CHANGED_BEFORE_UPLOAD')
        failure = None
        try:
            deploy(branch, sha, marker)
        except Exception:
            failure = 'DEPLOY_FAILED_UNCERTAIN'
        # A failed CLI may have uploaded. Always attempt the Production post-check.
        require(snapshot(api, branch) == before, 'PRODUCTION_CHANGED_AFTER_UPLOAD')
        require(failure is None, failure)
        try:
            state = 'not_found'
            for _ in range(12):
                matches = []
                for d in api.deployments('preview'):
                    meta = d.get('deployment_trigger', {}).get('metadata', {})
                    if d['id'] not in old_ids and meta.get('commit_message') == marker:
                        matches.append(d)
                require(len(matches) <= 1, 'AMBIGUOUS_DEPLOYMENT')
                state = 'not_found'
                if matches:
                    d = matches[0]
                    meta = d['deployment_trigger']['metadata']
                    require(d['environment'] == 'preview' and meta.get('branch') == branch and meta.get('commit_hash') == sha, 'DEPLOYMENT_MISMATCH')
                    status = d.get('latest_stage', {})
                    require(status.get('status') not in ('failure', 'canceled'), 'DEPLOYMENT_FAILED')
                    state = 'pending'
                    if status.get('name') == 'deploy' and status.get('status') == 'success':
                        require(valid_url(d.get('url')), 'URL_REJECTED')
                        alias = f'https://{branch}.{PROJECT}.pages.dev'
                        aliases = d.get('aliases')
                        require(isinstance(aliases, list) and alias in aliases, 'ALIAS_MISSING_OR_WRONG')
                        # Preserve the validated destination even if public access is denied.
                        # This is API evidence only; the final Production check is still pending.
                        print(json.dumps({'stage': 'deployment_api_verified', 'project': PROJECT,
                                          'branch': branch, 'commit': sha, 'marker': marker,
                                          'environment': 'preview', 'url': d['url'], 'alias_url': alias,
                                          'deployment_api_verified': True, 'public_content_verified': False,
                                          'production_final_check': 'pending'}, sort_keys=True), flush=True)
                        expected = hashlib.sha256((stage / 'assets/index.html').read_bytes()).hexdigest()
                        state = 'unverified'
                        verified = True
                        for role, url in (('deployment', d['url']), ('alias', alias)):
                            result = probe(url, expected, role)
                            print(json.dumps(result, sort_keys=True))
                            # Never retry access denials or attempt the other URL as a bypass.
                            require(result['state'] != 'access_denied', 'PUBLIC_ACCESS_DENIED')
                            require(result['state'] != 'redirect_rejected', 'PUBLIC_REDIRECT_REJECTED')
                            verified = verified and (result['state'] == 'verified' and
                                result['http_status'] == 200 and result['hash_match'] is True)
                        if verified:
                            return {'project': PROJECT, 'branch': branch, 'commit': sha, 'environment': 'preview',
                                    'url': d['url'], 'alias_url': alias, 'manifest_sha256': digest, 'production_unchanged': True}
                pause(5)
            raise Refused({'not_found': 'DEPLOYMENT_NOT_FOUND', 'pending': 'DEPLOYMENT_PENDING',
                           'unverified': 'DEPLOYMENT_UNVERIFIED'}[state])
        finally:
            # Includes access denial, malformed metadata, pending, and probe/API errors.
            # A changed/unknown Production state must override every verification outcome.
            require(snapshot(api, branch) == before, 'PRODUCTION_CHANGED_AFTER_UPLOAD')

def child_pids():
    # Single-threaded Linux uploader owns all children while this supervisor runs.
    mine = os.getpid()
    direct = Path(f'/proc/self/task/{mine}/children')
    if direct.exists():
        return [int(pid) for pid in direct.read_text().split()]
    # Some Linux sandboxes omit task/children. Read only stat PPIDs, never
    # cmdline/environ or credentials. Subreaper adoption remains kernel-managed.
    result = []
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            try:
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                if int(fields[1]) == mine:
                    result.append(int(entry.name))
            except FileNotFoundError:
                pass  # Process exited during enumeration.
    return result

def run_uploader(args, *, cwd, env, timeout=600, grace=0.5):
    """Own/reap the complete uploader tree, including double-forked descendants.

    Do not return (hence no API postcheck or flock release) before all children
    are gone. SIGKILL/host loss cannot be handled; runner tracking is a backstop.
    """
    require(sys.platform == 'linux' and not child_pids(), 'SUPERVISOR_UNAVAILABLE')
    libc = ctypes.CDLL(None, use_errno=True)
    prior = ctypes.c_int()
    require(libc.prctl(37, ctypes.byref(prior), 0, 0, 0) == 0, 'SUPERVISOR_UNAVAILABLE') # PR_GET_CHILD_SUBREAPER
    require(libc.prctl(36, 1, 0, 0, 0) == 0, 'SUPERVISOR_UNAVAILABLE') # PR_SET_CHILD_SUBREAPER
    cancelled = False
    def cancel(_signum, _frame):
        nonlocal cancelled
        cancelled = True  # Do not interrupt spawn/cleanup and lose process ownership.
    handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)}
    proc = None
    failed = None
    try:
        proc = subprocess.Popen(args, cwd=cwd, env=env, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            if cancelled or time.monotonic() >= deadline:
                failed = 'DEPLOY_CANCELLED' if cancelled else 'DEPLOY_TIMEOUT'
                break
            time.sleep(0.02)
        if failed is None:
            if proc.returncode != 0:
                failed = 'DEPLOY_FAILED'
            elif child_pids():
                failed = 'DEPLOY_DESCENDANTS_LEFT'
    finally:
        if proc is not None:
            # Signal the whole original group, then every adopted descendant.
            # Subreaper adoption also covers children that changed session/group.
            end_grace = time.monotonic() + grace
            while True:
                proc.poll()  # Reap the direct child through Popen, preserving its status.
                children = child_pids()
                sig = signal.SIGTERM if time.monotonic() < end_grace else signal.SIGKILL
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    pass
                for pid in children:
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
                    if pid != proc.pid:
                        try:
                            os.waitpid(pid, os.WNOHANG)
                        except ChildProcessError:
                            pass
                if proc.poll() is not None and not child_pids():
                    break
                # No cleanup timeout: never unlock with a possibly live uploader.
                # An unkillable process holds the job until the runner terminates it.
                time.sleep(0.02)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        require(libc.prctl(36, prior.value, 0, 0, 0) == 0, 'SUPERVISOR_RESTORE_FAILED')
    require(not cancelled, 'DEPLOY_CANCELLED')
    require(failed is None, failed)

def wrangler_upload(stage, tool_dir, account, token):
    def deploy(branch, sha, marker):
        # A fresh cwd outside repository ancestry prevents discovery of site configs,
        # functions, package scripts, .env, .dev.vars and Git hooks.
        with tempfile.TemporaryDirectory(prefix='roy-upload-') as tmp:
            work = Path(tmp)
            shutil.copytree(stage / 'assets', work / 'assets')
            env = {'PATH': os.environ['PATH'], 'HOME': str(work / 'home'), 'CI': 'true',
                   'CLOUDFLARE_ACCOUNT_ID': account, 'CLOUDFLARE_API_TOKEN': token,
                   'WRANGLER_SEND_METRICS': 'false', 'WRANGLER_LOG': 'error',
                   'WRANGLER_LOG_PATH': str(work / 'wrangler.log')}
            if 'RUNNER_TRACKING_ID' in os.environ:
                env['RUNNER_TRACKING_ID'] = os.environ['RUNNER_TRACKING_ID']
            args = ['node', str(tool_dir / 'node_modules/wrangler/bin/wrangler.js'), 'pages', 'deploy', str(work / 'assets'),
                    '--project-name', PROJECT, '--branch', branch, '--commit-hash', sha,
                    '--commit-message', marker, '--commit-dirty=false']
            # Suppress CLI output, not just token masking. Never upload Wrangler logs.
            run_uploader(args, cwd=work, env=env)
    return deploy

def main():
    try:
        mode = sys.argv[1]
        branch = os.environ.get('PREVIEW_BRANCH', '')
        sha = os.environ.get('CONTENT_SHA', '')
        branch_ok(branch)
        sha_ok(sha)
        root = Path(os.environ['GITHUB_WORKSPACE'])
        temp = Path(os.environ['RUNNER_TEMP'])
        stage = temp / 'roy-preview-stage'
        if mode == 'prepare':
            prepare(root, stage, branch, sha)
            print('STATIC_ASSETS_VALIDATED')
        elif mode == 'publish':
            require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY and os.environ.get('GITHUB_REF') == 'refs/heads/main'
                    and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'CONTEXT_REJECTED')
            token = os.environ['CLOUDFLARE_API_TOKEN']
            account = os.environ['CLOUDFLARE_ACCOUNT_ID']
            marker = 'roy-preview-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
            report = publish(API(account, token), wrangler_upload(stage, root / 'tools/pages-preview', account, token),
                             stage, branch, sha, marker, temp / 'roy-pages-publisher.lock')
            print(json.dumps(report, sort_keys=True))
        else:
            raise Refused('MODE_REJECTED')
        return 0
    except Refused as e:
        print('STOP: ' + str(e), file=sys.stderr)
    except Exception:
        print('STOP: INTERNAL_VALIDATION_FAILURE', file=sys.stderr)
    return 1

if __name__ == '__main__':
    sys.exit(main())
