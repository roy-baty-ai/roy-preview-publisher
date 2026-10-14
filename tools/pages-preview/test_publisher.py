import copy
import io
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import threading
import time
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
import publisher as p

SHA = 'a' * 40
TOKEN = 'FAKE_SECRET_MUST_NOT_APPEAR'
MARKER = 'roy-preview-123-1'

def probe_result(match=True, role='deployment'):
    return p.probe_result(role, 'verified' if match else 'content_mismatch', 200, hash_match=match)

class FakeAPI:
    def __init__(self, branch='reserved-prod'):
        self.project = {'name': p.PROJECT, 'subdomain': p.PROJECT + '.pages.dev',
                        'production_branch': branch, 'canonical_deployment': None, 'source': None,
                        'deployment_configs': {'production': {'env_vars': {}, 'compatibility_flags': []}}}
        self.prod = []
        self.preview = []
        self.calls = 0
    def get(self):
        self.calls += 1
        return copy.deepcopy(self.project)
    def deployments(self, env):
        return copy.deepcopy(self.prod if env == 'production' else self.preview)
    def deploy(self, branch, sha, marker):
        self.preview.append({'id': 'new-deployment', 'environment': 'preview',
                             'url': 'https://1234abcd.roy-web-sales-previews.pages.dev',
                             'aliases': [f'https://{branch}.roy-web-sales-previews.pages.dev'],
                             'deployment_trigger': {'metadata': {'branch': branch, 'commit_hash': sha, 'commit_message': marker}},
                             'latest_stage': {'name': 'deploy', 'status': 'success'}})

class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stage = self.root / 'stage'
        (self.stage / 'assets').mkdir(parents=True)
        (self.stage / 'assets/index.html').write_text('<!doctype html><title>Fixture</title>')
        (self.stage / 'assets/_headers').write_bytes(b'/*\n  X-Robots-Tag: noindex, nofollow, noarchive\n')
        self.manifest()
        self.api = FakeAPI()
    def manifest(self, branch='glass-green'):
        m = {'branch': branch, 'commit': SHA, 'files': {
            f.name: p.hashlib.sha256(f.read_bytes()).hexdigest() for f in (self.stage / 'assets').iterdir()}}
        (self.stage / 'manifest.json').write_text(json.dumps(m))
    def publish(self, branch='glass-green', deploy=None):
        return p.publish(self.api, deploy or self.api.deploy, self.stage, branch, SHA, MARKER, self.root / 'lock', pause=lambda _: None, probe=lambda url, expected, role: probe_result(role=role))
    def test_window_door_preview_success_preserves_other_preview(self):
        self.manifest('window-door-demo')
        self.api.deploy('reamertec', 'b' * 40, 'roy-preview-100-1')
        self.api.preview[0]['id'] = 'existing-other-preview'
        existing = copy.deepcopy(self.api.preview[0])
        visited = []
        result = p.publish(self.api, self.api.deploy, self.stage, 'window-door-demo', SHA, MARKER,
                           self.root / 'lock', pause=lambda _: None,
                           probe=lambda url, expected, role: visited.append(url) or probe_result(role=role))
        self.assertEqual(result['environment'], 'preview')
        self.assertEqual(result['branch'], 'window-door-demo')
        self.assertEqual(result['alias_url'], 'https://window-door-demo.' + p.PROJECT + '.pages.dev')
        self.assertTrue(result['production_unchanged'])
        self.assertEqual(visited, [result['url'], result['alias_url']])
        self.assertEqual(self.api.preview[0], existing)
        self.assertEqual(len(self.api.preview), 2)

    def test_window_door_production_or_wrong_project_refused_before_upload(self):
        self.manifest('window-door-demo')
        for field, value, code in [('production_branch', 'window-door-demo', 'PRODUCTION_REJECTED'),
                                   ('production_branch', 'WINDOW-DOOR-DEMO', 'PRODUCTION_REJECTED'),
                                   ('name', 'other-project', 'PROJECT_REJECTED')]:
            self.api = FakeAPI()
            self.api.project[field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(p.Refused, code):
                self.publish('window-door-demo')
            self.assertEqual(self.api.preview, [])

    def test_window_door_wrong_alias_refused(self):
        self.manifest('window-door-demo')
        def deploy(*args):
            self.api.deploy(*args)
            self.api.preview[-1]['aliases'] = ['https://reamertec.' + p.PROJECT + '.pages.dev']
        with self.assertRaisesRegex(p.Refused, 'ALIAS_MISSING_OR_WRONG'):
            self.publish('window-door-demo', deploy=deploy)

    def test_window_door_variants_remain_unapproved(self):
        for branch in ('WINDOW-DOOR-DEMO', 'window-door-demo-2', '../window-door-demo',
                       'window-door-demo;echo secret', 'window-door-demo ', 'main', 'production'):
            with self.subTest(branch=branch), self.assertRaisesRegex(p.Refused, 'BRANCH_REJECTED'):
                self.publish(branch)
        self.assertEqual(self.api.preview, [])

    def test_window_door_cli_context_rejected_before_secret_use(self):
        env = {'PREVIEW_BRANCH': 'window-door-demo', 'CONTENT_SHA': SHA,
               'GITHUB_WORKSPACE': str(self.root), 'RUNNER_TEMP': str(self.root),
               'GITHUB_REPOSITORY': p.REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
               'GITHUB_EVENT_NAME': 'workflow_dispatch'}
        for key, value in [('GITHUB_REF', 'refs/heads/proposal/window-door-demo-publisher'),
                           ('GITHUB_REF', 'refs/tags/main'), ('GITHUB_REPOSITORY', 'other/repo'),
                           ('GITHUB_EVENT_NAME', 'push'), ('GITHUB_EVENT_NAME', 'pull_request')]:
            err = io.StringIO()
            with self.subTest(key=key, value=value), patch.dict(os.environ, dict(env, **{key: value}), clear=True), \
                 patch('sys.argv', ['publisher', 'publish']), patch('sys.stderr', err), patch.object(p, 'API') as api:
                self.assertEqual(p.main(), 1)
                self.assertEqual(err.getvalue().strip(), 'STOP: CONTEXT_REJECTED')
                api.assert_not_called()

    def test_success_no_production(self):
        self.assertTrue(self.publish()['production_unchanged'])
    def test_unexpected_existing_production(self):
        d = {'id': 'production-old', 'environment': 'production', 'url': 'https://production.example'}
        self.api.project['canonical_deployment'] = d
        self.api.prod = [d]
        with self.assertRaisesRegex(p.Refused, 'UNEXPECTED_EXISTING_PRODUCTION'):
            self.publish()
        self.assertEqual(self.api.preview, [])
    def test_public_probe_must_succeed(self):
        with self.assertRaisesRegex(p.Refused, 'DEPLOYMENT_UNVERIFIED'):
            p.publish(self.api, self.api.deploy, self.stage, 'glass-green', SHA, MARKER,
                      self.root / 'lock', pause=lambda _: None, probe=lambda url, expected, role: probe_result(False, role))
    def test_alias_missing_wrong_or_old(self):
        for aliases in (None, [], ['https://reamertec.' + p.PROJECT + '.pages.dev'],
                        ['https://glass-green.' + p.PROJECT + '.pages.dev.evil.example']):
            self.api = FakeAPI()
            def deploy(*args):
                self.api.deploy(*args)
                self.api.preview[-1]['aliases'] = aliases
            with self.subTest(aliases=aliases), self.assertRaisesRegex(p.Refused, 'ALIAS_MISSING_OR_WRONG'):
                self.publish(deploy=deploy)
        # Immutable URL serves current bytes, while the public alias serves old/wrong bytes.
        for wrong in (b'previous approved version', b'wrong content'):
            self.api = FakeAPI()
            def probe(url, expected, role):
                data = wrong if 'glass-green.' in url else (self.stage / 'assets/index.html').read_bytes()
                return probe_result(p.hashlib.sha256(data).hexdigest() == expected, role)
            with self.assertRaisesRegex(p.Refused, 'DEPLOYMENT_UNVERIFIED'):
                p.publish(self.api, self.api.deploy, self.stage, 'glass-green', SHA, MARKER,
                          self.root / 'lock', pause=lambda _: None, probe=probe)
    def test_both_public_urls_verified(self):
        visited = []
        def probe(url, expected, role):
            visited.append(url)
            return probe_result(role=role)
        result = p.publish(self.api, self.api.deploy, self.stage, 'glass-green', SHA, MARKER,
                           self.root / 'lock', pause=lambda _: None, probe=probe)
        self.assertEqual(visited, [result['url'], result['alias_url']])
        self.assertEqual(result['alias_url'], 'https://glass-green.' + p.PROJECT + '.pages.dev')
    def test_empty_unknown_and_production_branches(self):
        for branch in ('', 'main', 'production', 'other', 'GLASS-GREEN', 'glass-green;echo secret', '../reamertec'):
            with self.subTest(branch=branch), self.assertRaises(p.Refused):
                self.publish(branch)
        for branch in p.BRANCHES:
            self.api = FakeAPI(branch)
            with self.assertRaisesRegex(p.Refused, 'PRODUCTION_REJECTED'):
                p.snapshot(self.api, branch)
        self.assertEqual(self.api.preview, [])
    def test_unknown_production_and_git_connected(self):
        for prod in ('', None, ' main '):
            self.api = FakeAPI(prod)
            with self.assertRaises(p.Refused):
                self.publish()
        self.api = FakeAPI()
        self.api.project['source'] = {'type': 'github'}
        with self.assertRaises(p.Refused):
            self.publish()
    def test_api_failure_prevents_upload(self):
        with patch.object(self.api, 'get', side_effect=p.Refused('API_FAILED')):
            with self.assertRaisesRegex(p.Refused, 'API_FAILED'):
                self.publish()
        self.assertEqual(self.api.preview, [])
    def test_deploy_failure_and_postcheck(self):
        def fail(*_):
            raise RuntimeError(TOKEN)
        with self.assertRaisesRegex(p.Refused, '^DEPLOY_FAILED_UNCERTAIN$'):
            self.publish(deploy=fail)
        self.assertEqual(self.api.calls, 3)
    def test_production_changed_during_upload(self):
        def bad(*args):
            self.api.deploy(*args)
            self.api.prod.append({'id': 'new-prod', 'environment': 'production'})
        with self.assertRaisesRegex(p.Refused, 'PRODUCTION_CHANGED_AFTER_UPLOAD'):
            self.publish(deploy=bad)
    def test_production_changed_before_upload(self):
        orig = self.api.get
        def changed():
            result = orig()
            if self.api.calls > 1:
                result['production_branch'] = 'new-prod'
            return result
        with patch.object(self.api, 'get', side_effect=changed), self.assertRaisesRegex(p.Refused, 'PRODUCTION_CHANGED_BEFORE_UPLOAD'):
            self.publish()
        self.assertEqual(self.api.preview, [])
    def test_post_upload_api_failure(self):
        def deploy(*args):
            self.api.deploy(*args)
            self.api.get = lambda: (_ for _ in ()).throw(p.Refused('API_FAILED'))
        with self.assertRaisesRegex(p.Refused, 'API_FAILED'):
            self.publish(deploy=deploy)
    def test_mismatch_failure_timeout_and_unsafe_url(self):
        for field, value in [('branch', 'reamertec'), ('commit_hash', 'b' * 40), ('url', 'https://attacker.example'),
                             ('status', 'failure'), ('commit_message', 'other')]:
            self.api = FakeAPI()
            def bad(*args):
                self.api.deploy(*args)
                d = self.api.preview[0]
                if field == 'url': d[field] = value
                elif field == 'status': d['latest_stage'][field] = value
                else: d['deployment_trigger']['metadata'][field] = value
            with self.subTest(field=field), self.assertRaises(p.Refused):
                self.publish(deploy=bad)
    def test_parallel_process_lock(self):
        with p.exclusive(self.root / 'lock'):
            r = subprocess.run(['python3', '-c',
                'import publisher as p,sys;\ntry:\n with p.exclusive(sys.argv[1]): pass\nexcept p.Refused: sys.exit(42)',
                str(self.root / 'lock')], cwd=Path(__file__).parent, capture_output=True)
            self.assertEqual(r.returncode, 42)
        self.assertTrue(self.publish()['production_unchanged'])
    def test_invalid_paths_and_site_execution_inputs(self):
        for name in ('../index.html', '/index.html', '.env', '_worker.js', 'functions/x.js', 'package.json',
                     'wrangler.toml', 'file.sh', 'a//b.html', 'a/../b.html', '_redirects', 'a\\b.html'):
            with self.subTest(name=name), self.assertRaises(p.Refused):
                p.file_ok(name)
    def test_tamper_and_symlink(self):
        (self.stage / 'assets/index.html').write_text('changed')
        with self.assertRaisesRegex(p.Refused, 'ASSET_CHANGED'):
            self.publish()
        (self.stage / 'assets/index.html').unlink()
        (self.stage / 'assets/index.html').symlink_to('/etc/hosts')
        with self.assertRaisesRegex(p.Refused, 'SYMLINK_REJECTED'):
            self.publish()
    def test_api_redacts_errors_and_rejects_redirect(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=RuntimeError(TOKEN)):
            with self.assertRaisesRegex(p.Refused, '^API_FAILED$'):
                p.API('a' * 32, TOKEN).get()
        with self.assertRaises(p.Refused):
            p.NoRedirect().redirect_request(None, None, None, None, None, None)
    def test_cli_no_secret_in_logs(self):
        env = {'PREVIEW_BRANCH': 'glass-green', 'CONTENT_SHA': SHA, 'GITHUB_WORKSPACE': str(self.root),
               'RUNNER_TEMP': str(self.root), 'GITHUB_REPOSITORY': p.REPOSITORY,
               'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
               'CLOUDFLARE_API_TOKEN': TOKEN, 'CLOUDFLARE_ACCOUNT_ID': 'a' * 32,
               'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch('sys.argv', ['publisher', 'publish']), \
             patch('sys.stdout', out), patch('sys.stderr', err), patch.object(p, 'publish', side_effect=RuntimeError(TOKEN)):
            self.assertEqual(p.main(), 1)
        self.assertNotIn(TOKEN, out.getvalue() + err.getvalue())
        self.assertIn('INTERNAL_VALIDATION_FAILURE', err.getvalue())
    def test_http_api_envelope_failures(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, *_): return self.payload
        r = Response()
        api = p.API('a' * 32, TOKEN)
        for payload in (b'not-json', b'{"success":false,"result":null}',
                        b'{"success":true,"errors":[{"message":"secret"}],"result":{}}',
                        b'{"success":true}'):
            r.payload = payload
            with patch('urllib.request.OpenerDirector.open', return_value=r), self.assertRaises(p.Refused):
                api.get()
        r.payload = b'{"success":true,"errors":[],"result":{"name":"fixture"}}'
        with patch('urllib.request.OpenerDirector.open', return_value=r) as opened:
            self.assertEqual(api.get()['name'], 'fixture')
            self.assertEqual(opened.call_args.args[0].full_url,
                'https://api.cloudflare.com/client/v4/accounts/' + 'a' * 32 + '/pages/projects/' + p.PROJECT)
    def test_pagination_does_not_ignore_production(self):
        api = p.API('a' * 32, TOKEN)
        rows = [{'id': str(i), 'environment': 'production'} for i in range(10)]
        with patch.object(api, 'get', side_effect=[rows, [{'id': 'last', 'environment': 'production'}]]) as get:
            self.assertEqual(len(api.deployments('production')), 11)
            self.assertIn('page=2', get.call_args.args[0])
        with patch.object(api, 'get', return_value=[{'id': 'wrong', 'environment': 'preview'}]), self.assertRaises(p.Refused):
            api.deployments('production')
    def test_pagination_documented_example_boundaries(self):
        from urllib.parse import urlsplit, parse_qs
        for count, expected_requests in ((0,1),(9,1),(10,2),(11,2),(20,3)):
            api = p.API('a' * 32, TOKEN)
            rows = [{'id': str(i), 'environment': 'production'} for i in range(count)]
            def get(suffix):
                query = parse_qs(urlsplit(suffix).query)
                self.assertEqual(query['per_page'], ['10'])
                self.assertEqual(query['env'], ['production'])
                page = int(query['page'][0]); self.assertGreaterEqual(page,1)
                return rows[(page-1)*10:page*10]
            with self.subTest(count=count), patch.object(api,'get',side_effect=get) as mocked:
                self.assertEqual(api.deployments('production'), rows)
                self.assertEqual(mocked.call_count, expected_requests)
    def test_pagination_bound_still_fails_closed(self):
        api = p.API('a' * 32, TOKEN)
        rows = [{'id': str(i), 'environment': 'production'} for i in range(10)]
        with patch.object(api,'get',return_value=rows) as mocked, self.assertRaisesRegex(p.Refused,'PAGINATION_LIMIT'):
            api.deployments('production')
        self.assertEqual(mocked.call_count,100)
    def test_missing_fields_and_wrong_project(self):
        for field in ('production_branch', 'canonical_deployment', 'name', 'subdomain'):
            self.api = FakeAPI()
            del self.api.project[field]
            with self.subTest(field=field), self.assertRaises(p.Refused):
                self.publish()
        self.assertEqual(self.api.preview, [])
    def test_production_config_required(self):
        for configs in (None, [], 'bad', {}, {'production': None}, {'production': []}, {'production': 'bad'}):
            self.api = FakeAPI()
            self.api.project['deployment_configs'] = configs
            with self.subTest(configs=configs), self.assertRaisesRegex(p.Refused, 'PRODUCTION_CONFIG_UNKNOWN'):
                self.publish()
            self.assertEqual(self.api.preview, [])
        self.api = FakeAPI()
        del self.api.project['deployment_configs']
        with self.assertRaisesRegex(p.Refused, 'PRODUCTION_CONFIG_UNKNOWN'):
            self.publish()
        self.assertEqual(self.api.preview, [])
    def test_production_config_change_rejected(self):
        def deploy(*args):
            self.api.deploy(*args)
            self.api.project['deployment_configs']['production']['compatibility_flags'] = ['changed']
        with self.assertRaisesRegex(p.Refused, 'PRODUCTION_CHANGED_AFTER_UPLOAD'):
            self.publish(deploy=deploy)
    def test_oversize_and_manifest_mismatch(self):
        with patch.object(p, 'MAX_FILE', 1), self.assertRaises(p.Refused):
            self.publish()
        m = json.loads((self.stage / 'manifest.json').read_text())
        m['branch'] = 'reamertec'
        (self.stage / 'manifest.json').write_text(json.dumps(m))
        with self.assertRaisesRegex(p.Refused, 'MANIFEST_MISMATCH'):
            self.publish()
    def test_public_probe_has_no_auth_and_checks_bytes(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, *_): return b'fixture'
        with patch('urllib.request.OpenerDirector.open', return_value=Response()) as opened:
            self.assertEqual(p.public_probe('https://1234abcd.' + p.PROJECT + '.pages.dev', p.hashlib.sha256(b'fixture').hexdigest()), probe_result())
            self.assertNotIn('Authorization', dict(opened.call_args.args[0].header_items()))
            self.assertEqual(p.public_probe('https://1234abcd.' + p.PROJECT + '.pages.dev', 'wrong'), probe_result(False))
    def test_probe_errors_safe_and_single_request(self):
        cases = [
            (urllib.error.HTTPError('https://bad.invalid', 403, TOKEN, {},
                io.BytesIO(('error code: 1010 ' + TOKEN).encode())), 'access_denied', 403, [1010]),
            (urllib.error.HTTPError('https://bad.invalid', 403, TOKEN, {},
                io.BytesIO(('error code: 9999 ' + TOKEN).encode())), 'access_denied', 403, []),
            (urllib.error.HTTPError('https://bad.invalid', 302, TOKEN, {}, io.BytesIO(TOKEN.encode())),
                'redirect_rejected', 302, []),
            (p.RedirectRejected(307), 'redirect_rejected', 307, []),
            (urllib.error.URLError(TOKEN), 'network_failure', None, []),
            (urllib.error.HTTPError('https://bad.invalid', 503, TOKEN, {}, io.BytesIO(TOKEN.encode())),
                'http_error', 503, []),
        ]
        for error, state, status, codes in cases:
            with self.subTest(state=state, status=status), patch('urllib.request.OpenerDirector.open', side_effect=error) as opened:
                result = p.public_probe('https://1234abcd.' + p.PROJECT + '.pages.dev', 'unused', 'alias')
                self.assertEqual(result, p.probe_result('alias', state, status, codes))
                opened.assert_called_once()
                self.assertNotIn(TOKEN, json.dumps(result))
                self.assertNotIn('invalid', json.dumps(result))
        response = unittest.mock.Mock()
        with self.assertRaises(p.RedirectRejected) as error:
            p.NoRedirect().redirect_request(None, response, 302, TOKEN, {}, 'https://bad.invalid')
        self.assertEqual(error.exception.status, 302)
        response.close.assert_called_once()

    def test_denial_and_redirect_do_not_retry_and_always_postcheck(self):
        for role in ('deployment', 'alias'):
            for state, code in (('access_denied', 'PUBLIC_ACCESS_DENIED'), ('redirect_rejected', 'PUBLIC_REDIRECT_REJECTED')):
                self.api = FakeAPI()
                visited, pauses = [], []
                def probe(url, expected, current_role):
                    visited.append(current_role)
                    return p.probe_result(current_role, state, 403, [1010]) if role == current_role else probe_result(role=current_role)
                with self.subTest(role=role, state=state), self.assertRaisesRegex(p.Refused, code):
                    p.publish(self.api, self.api.deploy, self.stage, 'glass-green', SHA, MARKER,
                              self.root / 'lock', pause=pauses.append, probe=probe)
                self.assertEqual(visited, ['deployment'] if role == 'deployment' else ['deployment', 'alias'])
                self.assertEqual(pauses, [])
                self.assertEqual(self.api.calls, 4)

    def test_api_url_record_is_flushed_before_denied_probe_without_secrets(self):
        class FlushedOutput(io.StringIO):
            flushes = 0
            def flush(self):
                self.flushes += 1
                super().flush()
        out, visited, pauses = FlushedOutput(), [], []
        def deploy(*args):
            self.api.deploy(*args)
            self.api.preview[0]['private_response_field'] = TOKEN
            self.api.preview[0]['deployment_trigger']['metadata']['private_field'] = TOKEN
        def probe(url, expected, role):
            visited.append(role)
            record = json.loads(out.getvalue())
            self.assertGreater(out.flushes, 0)
            self.assertEqual(record, {
                'stage': 'deployment_api_verified', 'project': p.PROJECT,
                'branch': 'glass-green', 'commit': SHA, 'marker': MARKER,
                'environment': 'preview', 'url': url,
                'alias_url': 'https://glass-green.' + p.PROJECT + '.pages.dev',
                'deployment_api_verified': True, 'public_content_verified': False,
                'production_final_check': 'pending'})
            return p.probe_result(role, 'access_denied', 403, [1010])
        with patch('sys.stdout', out), self.assertRaisesRegex(p.Refused, '^PUBLIC_ACCESS_DENIED$'):
            p.publish(self.api, deploy, self.stage, 'glass-green', SHA, MARKER,
                      self.root / 'lock', pause=pauses.append, probe=probe)
        self.assertEqual(visited, ['deployment'])
        self.assertEqual(pauses, [])
        self.assertEqual(self.api.calls, 4)
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertFalse(json.loads(out.getvalue().splitlines()[0])['public_content_verified'])

    def test_api_url_record_not_emitted_before_deployment_validation(self):
        for field, value in (('branch', 'reamertec'), ('commit_hash', 'b' * 40),
                             ('commit_message', 'other'), ('environment', 'production'),
                             ('status', 'active'), ('status', 'failure'),
                             ('url', 'https://evil.invalid/' + TOKEN), ('aliases', [])):
            self.api = FakeAPI()
            out = io.StringIO()
            def deploy(*args):
                self.api.deploy(*args)
                item = self.api.preview[0]
                if field in ('branch', 'commit_hash', 'commit_message'):
                    item['deployment_trigger']['metadata'][field] = value
                elif field == 'status':
                    item['latest_stage'][field] = value
                else:
                    item[field] = value
            with self.subTest(field=field, value=value), patch('sys.stdout', out), self.assertRaises(p.Refused):
                self.publish(deploy=deploy)
            self.assertEqual(out.getvalue(), '')
            self.assertEqual(self.api.calls, 4)

    def test_api_record_does_not_claim_final_production_check_on_change(self):
        out = io.StringIO()
        def probe(url, expected, role):
            self.api.project['production_branch'] = 'changed'
            return probe_result(role=role)
        with patch('sys.stdout', out), self.assertRaisesRegex(p.Refused, 'PRODUCTION_CHANGED_AFTER_UPLOAD'):
            p.publish(self.api, self.api.deploy, self.stage, 'glass-green', SHA, MARKER,
                      self.root / 'lock', pause=lambda _: None, probe=probe)
        record = json.loads(out.getvalue().splitlines()[0])
        self.assertTrue(record['deployment_api_verified'])
        self.assertFalse(record['public_content_verified'])
        self.assertEqual(record['production_final_check'], 'pending')
        self.assertNotIn('production_unchanged', record)
        self.assertEqual(self.api.calls, 4)

    def test_production_change_overrides_denial_and_probe_exception(self):
        for raises in (False, True):
            self.api = FakeAPI()
            def probe(*args):
                self.api.project['production_branch'] = 'changed'
                if raises:
                    raise RuntimeError(TOKEN)
                return p.probe_result('deployment', 'access_denied', 403, [1010])
            with self.assertRaisesRegex(p.Refused, 'PRODUCTION_CHANGED_AFTER_UPLOAD'):
                p.publish(self.api, self.api.deploy, self.stage, 'glass-green', SHA, MARKER,
                          self.root / 'lock', pause=lambda _: None, probe=probe)
            self.assertEqual(self.api.calls, 4)

    def test_not_found_and_pending_distinct_with_final_postcheck(self):
        for pending, code in ((False, 'DEPLOYMENT_NOT_FOUND'), (True, 'DEPLOYMENT_PENDING')):
            self.api = FakeAPI()
            def deploy(*args):
                if pending:
                    self.api.deploy(*args)
                    self.api.preview[0]['latest_stage']['status'] = 'active'
            with self.assertRaisesRegex(p.Refused, code):
                self.publish(deploy=deploy)
            self.assertEqual(self.api.calls, 4)

    def test_metadata_refusal_runs_final_postcheck(self):
        def deploy(*args):
            self.api.deploy(*args)
            self.api.preview[0]['deployment_trigger']['metadata']['commit_hash'] = 'bad'
        with self.assertRaisesRegex(p.Refused, 'DEPLOYMENT_MISMATCH'):
            self.publish(deploy=deploy)
        self.assertEqual(self.api.calls, 4)

    def test_wrangler_has_no_shell_and_minimal_env(self):
        with patch.object(p, 'run_uploader') as run, patch.dict(os.environ, {'RUNNER_TRACKING_ID': 'fixture-tracker'}):
            p.wrangler_upload(self.stage, self.root, 'a' * 32, TOKEN)('glass-green', SHA, MARKER)
            args, kw = run.call_args
            self.assertNotIn(TOKEN, str(args))
            self.assertFalse(kw.get('shell', False))
            self.assertNotIn('GITHUB_TOKEN', kw['env'])
            self.assertNotIn('NODE_OPTIONS', kw['env'])
            self.assertEqual(kw['env']['RUNNER_TRACKING_ID'], 'fixture-tracker')
            self.assertIn('--branch', args[0])

class GitArtifactTests(unittest.TestCase):
    def test_window_door_extracts_only_selected_static_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.check_output(['git', '-C', tmp, *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'user.name', 'Fixture')
            assets = root / 'preview-assets/window-door-demo'
            (assets / 'assets').mkdir(parents=True)
            (assets / 'index.html').write_text('<html>Fictional demo</html>')
            (assets / 'assets/window.png').write_bytes(b'local-image-fixture')
            other = root / 'preview-assets/reamertec'
            other.mkdir(parents=True)
            (other / 'index.html').write_text('Do not include other preview')
            (root / 'verification.txt').write_text('Do not publish internal verification')
            git('add', '.')
            git('commit', '-m', 'fixture')
            sha = git('rev-parse', 'HEAD')
            git('update-ref', p.CONTENT_REF, sha)
            stage = root / 'stage'
            manifest = p.prepare(root, stage, 'window-door-demo', sha)
            p.verify_stage(stage, 'window-door-demo', sha)
            self.assertEqual(set(manifest['files']), {'index.html', 'assets/window.png', '_headers'})
            self.assertEqual((stage / 'assets/index.html').read_bytes(), (assets / 'index.html').read_bytes())
            (assets / '_worker.js').write_text('must be rejected')
            git('add', 'preview-assets')
            git('commit', '-m', 'forbidden')
            bad = git('rev-parse', 'HEAD')
            with self.assertRaises(p.Refused):
                p.prepare(root, root / 'unreviewed', 'window-door-demo', bad)
            git('update-ref', p.CONTENT_REF, bad)
            with self.assertRaisesRegex(p.Refused, 'PATH_REJECTED'):
                p.prepare(root, root / 'forbidden', 'window-door-demo', bad)

    def test_real_git_static_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                return subprocess.check_output(['git', '-C', tmp, *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'user.name', 'Fixture')
            assets = root / 'preview-assets/glass-green'
            assets.mkdir(parents=True)
            (assets / 'index.html').write_text('<html>local fixture</html>')
            (assets / 'app.js').write_text('throw new Error("must never execute")')
            git('add', '.')
            git('commit', '-m', 'fixture')
            sha = git('rev-parse', 'HEAD')
            git('update-ref', p.CONTENT_REF, sha)
            stage = root / 'stage'
            p.prepare(root, stage, 'glass-green', sha)
            p.verify_stage(stage, 'glass-green', sha)
            (assets / '_worker.js').write_text('malicious worker')
            git('add', 'preview-assets')
            git('commit', '-m', 'forbidden')
            bad = git('rev-parse', 'HEAD')
            # Not on the fixed content ref yet.
            with self.assertRaises(p.Refused):
                p.prepare(root, root / 'bad', 'glass-green', bad)
            git('update-ref', p.CONTENT_REF, bad)
            with self.assertRaises(p.Refused):
                p.prepare(root, root / 'bad', 'glass-green', bad)

class ProcessSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.case = PublisherTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.root
    def run_tree(self, mode):
        fixture = Path(__file__).parent.resolve() / 'fixtures/process_tree.py'
        p.run_uploader([sys.executable, str(fixture), 'wrapper', str(self.root), mode],
                       cwd=self.root, env={'PATH': os.defpath, 'RUNNER_TRACKING_ID': 'fixture'},
                       timeout=1.5, grace=0.05)
    def assert_tree_gone(self):
        pids = list(self.root.glob('*.pid'))
        self.assertEqual(len(pids), 3, 'fixture must really have spawned a child and detached grandchild')
        for file in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(int(file.read_text()), 0)
        self.assertEqual(p.child_pids(), [])
        heartbeats = {f.name: f.read_bytes() for f in self.root.glob('*.heartbeat')}
        time.sleep(0.1)
        self.assertEqual(heartbeats, {f.name: f.read_bytes() for f in self.root.glob('*.heartbeat')})
    def test_timeout_reaps_detached_descendants(self):
        with self.assertRaisesRegex(p.Refused, 'DEPLOY_TIMEOUT'):
            self.run_tree('hold')
        self.assert_tree_gone()
    def test_early_wrapper_success_is_not_upload_success(self):
        with self.assertRaisesRegex(p.Refused, 'DEPLOY_DESCENDANTS_LEFT'):
            self.run_tree('early-success')
        self.assert_tree_gone()
    def test_early_wrapper_failure_reaps_tree(self):
        with self.assertRaisesRegex(p.Refused, 'DEPLOY_FAILED'):
            self.run_tree('early-failure')
        self.assert_tree_gone()
    def test_cancel_reaps_tree(self):
        stop = threading.Event()
        def cancel_when_ready():
            while not stop.wait(0.005):
                if (self.root / 'ready').exists():
                    os.kill(os.getpid(), signal.SIGTERM)
                    return
        thread = threading.Thread(target=cancel_when_ready)
        thread.start()
        try:
            with self.assertRaisesRegex(p.Refused, 'DEPLOY_CANCELLED'):
                self.run_tree('hold')
        finally:
            stop.set()
            thread.join()
        self.assert_tree_gone()
    def test_postcheck_and_unlock_only_after_tree_exit(self):
        original = self.case.api.get
        checked = []
        def get():
            result = original()
            if self.case.api.calls >= 3:
                self.assert_tree_gone()
                with self.assertRaisesRegex(p.Refused, 'CONCURRENT_RUN'):
                    with p.exclusive(self.root / 'lock'):
                        pass
                checked.append(True)
            return result
        self.case.api.get = get
        with self.assertRaisesRegex(p.Refused, 'DEPLOY_FAILED_UNCERTAIN'):
            self.case.publish(deploy=lambda *_: self.run_tree('hold'))
        self.assertTrue(checked, 'Production postcheck must run after tree exit')
        with p.exclusive(self.root / 'lock'):
            self.assert_tree_gone()
    def test_clean_process_exit(self):
        p.run_uploader([sys.executable, '-c', 'pass'], cwd=self.root, env={'PATH': os.defpath}, timeout=2)
        self.assertEqual(p.child_pids(), [])

class WorkflowTests(unittest.TestCase):
    def test_installed_workflow_choices_and_guards_match_template(self):
        root = Path(__file__).resolve().parents[2]
        installed = (root / '.github/workflows/pages-preview.yml').read_text()
        template = (root / 'tools/pages-preview/workflow.yml.example').read_text()
        # Ignore introductory comments; the executable workflow must stay identical.
        self.assertEqual(installed[installed.index('name:'):], template[template.index('name:'):])
        options = p.re.search(r'options: \[(.*?)\]', installed).group(1).split(', ')
        self.assertEqual(options, ['glass-green', 'reamertec', 'window-door-demo'])
        self.assertEqual(tuple(options), p.BRANCHES)
        self.assertIn("github.ref == 'refs/heads/main'", installed)
        self.assertIn("github.repository == 'roy-baty-ai/roy-preview-publisher'", installed)
        self.assertIn("github.event_name == 'workflow_dispatch'", installed)
        self.assertIn('environment: cloudflare-preview-publish', installed)
        self.assertIn('permissions:\n  contents: read', installed)
        self.assertNotIn('push:', installed)
        self.assertNotIn('pull_request:', installed)
        self.assertEqual(installed.count('secrets.CLOUDFLARE_API_TOKEN'), 1)
        self.assertNotIn('secrets.', installed.split('- name: Validate, upload')[0])

    def test_inert_and_secret_scope(self):
        # Optional PyYAML parser check is performed separately in local verification.
        root = Path(__file__).parent
        text = (root / 'workflow.yml.example').read_text()
        self.assertNotIn('pull_request:', text)
        self.assertNotIn('push:', text)
        self.assertIn('workflow_dispatch:', text)
        self.assertIn('group: roy-web-sales-previews-publisher', text)
        self.assertIn('cancel-in-progress: false', text)
        self.assertEqual(text.count('secrets.CLOUDFLARE_API_TOKEN'), 1)
        self.assertNotIn('secrets.', text.split('- name: Validate, upload')[0])
        self.assertIn('persist-credentials: false', text)
        self.assertIn('python3 -I', text)

if __name__ == '__main__':
    unittest.main()
