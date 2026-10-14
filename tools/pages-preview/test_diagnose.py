import contextlib
import io
import json
import os
import urllib.error
import unittest
from unittest.mock import patch
import diagnose as d

TOKEN = 'FAKE_DIAGNOSTIC_SECRET'
ACCOUNT = 'a' * 32

class Response:
    def __init__(self, data, status=200, raw=None):
        self.status = status
        self.raw = raw if raw is not None else json.dumps({'success': True, 'errors': [], 'result': data}).encode()
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, size): return self.raw[:size]

class Opener:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    def open(self, request, timeout):
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception): raise response
        return response

def project():
    return {'name': d.PROJECT, 'subdomain': d.PROJECT + '.pages.dev', 'production_branch': 'reserved',
            'canonical_deployment': None, 'deployment_configs': {'production': {'secret': TOKEN}}}

def deployment(**metadata):
    return {'id': '00000000-0000-0000-0000-000000000001', 'environment': 'preview',
        'url': 'https://1234abcd.' + d.PROJECT + '.pages.dev', 'aliases': [d.ALIAS], 'deployment_trigger': {'metadata':
        dict(commit_message=d.MARKER, branch=d.BRANCH, commit_hash=d.CONTENT_SHA, **metadata)},
        'latest_stage': {'name': 'deploy', 'status': 'success'}, 'other_private_field': TOKEN}

class DiagnosticTests(unittest.TestCase):
    def execute(self, responses):
        opener=Opener(responses); out=io.StringIO()
        with contextlib.redirect_stdout(out): rc=d.run(ACCOUNT,TOKEN,opener)
        text=out.getvalue()
        self.assertNotIn(TOKEN,text); self.assertNotIn(ACCOUNT,text)
        return rc,[json.loads(x) for x in text.splitlines()],opener.calls
    def test_three_fixed_gets_only_and_safe_projection(self):
        rc,rows,calls=self.execute([Response(project()),Response([]),Response([deployment()])])
        self.assertEqual(rc,0);self.assertEqual(len(calls),3)
        self.assertEqual(rows[-1]['marker_state'],'api_verified')
        for request,suffix in zip(calls,('', '/deployments?env=production&per_page=10&page=1', '/deployments?env=preview&per_page=10&page=1')):
            self.assertEqual(request.method,'GET');self.assertIsNone(request.data)
            self.assertEqual(request.full_url,f'https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/pages/projects/{d.PROJECT}'+suffix)
    def test_list_queries_use_documented_example_size(self):
        from urllib.parse import urlsplit, parse_qs
        rc,rows,calls=self.execute([Response(project()),Response([]),Response([])])
        self.assertEqual(rc,1);self.assertEqual(len(calls),3)
        for request,env in zip(calls[1:],('production','preview')):
            self.assertEqual(parse_qs(urlsplit(request.full_url).query),
                             {'env':[env],'per_page':['10'],'page':['1']})
    def test_auth_error_numeric_code_only_no_retry(self):
        e=urllib.error.HTTPError('https://example.invalid',403,TOKEN,{},io.BytesIO(json.dumps({'errors':[{'code':10000,'message':TOKEN}]}).encode()))
        rc,rows,calls=self.execute([e])
        self.assertEqual(rc,1);self.assertEqual(len(calls),1)
        self.assertEqual(rows[0]['http_status'],403);self.assertEqual(rows[0]['cloudflare_error_codes'],[10000])
    def test_invalid_json_network_and_missing_result(self):
        for response in [Response(None,raw=b'not-json'),urllib.error.URLError(TOKEN),Response(None,raw=b'{"success":true}')]:
            rc,rows,calls=self.execute([response]);self.assertEqual(rc,1);self.assertEqual(len(calls),1)
    def test_redirect_never_followed(self):
        with self.assertRaises(d.Stop): d.NoRedirect().redirect_request(None,None,302,'',{},'https://evil.invalid')
        e=urllib.error.HTTPError('https://example.invalid',302,TOKEN,{'Location':'https://evil.invalid'},io.BytesIO(TOKEN.encode()))
        rc,rows,calls=self.execute([e]);self.assertEqual(rc,1);self.assertEqual(len(calls),1)
        self.assertEqual(rows[0]['http_status'],302);self.assertIsNone(rows[0]['json_valid'])
    def test_actual_redirect_handler_status_and_no_second_request(self):
        rc,rows,calls=self.execute([d.RedirectStopped(307)])
        self.assertEqual(rc,1);self.assertEqual(len(calls),1)
        self.assertEqual(rows[0]['http_status'],307);self.assertIsNone(rows[0]['json_valid'])
    def test_absence_after_complete_scan_is_not_success(self):
        rc,rows,calls=self.execute([Response(project()),Response([]),Response([])])
        self.assertEqual(rows[-1]['marker_state'],'not_found')
    def test_bad_metadata_never_printed(self):
        bad=deployment();bad['deployment_trigger']['metadata']['branch']=TOKEN
        rc,rows,_=self.execute([Response(project()),Response([]),Response([bad])])
        self.assertEqual(rows[-1]['marker_state'],'metadata_mismatch')
    def test_wrong_environment_stops(self):
        rc,rows,calls=self.execute([Response(project()),Response([{'environment':'preview'}])])
        self.assertEqual(rc,1);self.assertEqual(len(calls),2)
    def test_bound_body_and_error_code_types(self):
        rc,rows,calls=self.execute([Response(None,raw=b'x'*(d.MAX_BODY+1))])
        self.assertFalse(rows[0]['json_valid']);self.assertEqual(rc,1)
        self.assertEqual(d.error_codes({'errors':[{'code':TOKEN},{'code':True},{'code':10000}]}),[10000])
    def page(self, start, count=10):
        result = []
        for index in range(start, start + count):
            item = deployment()
            item['id'] = f'00000000-0000-0000-0000-{index:012x}'
            item['deployment_trigger']['metadata']['commit_message'] = 'other'
            result.append(item)
        return result

    def test_fixed_target_and_no_public_content_claim(self):
        self.assertEqual(d.BRANCH, 'window-door-demo')
        self.assertEqual(d.CONTENT_SHA, '2fc8dbbb62477d5cb44fd967495090ad77107941')
        self.assertEqual(d.MARKER, 'roy-preview-38377559460-1')
        rc, rows, _ = self.execute([Response(project()), Response([]), Response([deployment()])])
        self.assertEqual(rc, 0)
        self.assertFalse(rows[-1]['public_content_verified'])
        self.assertEqual(rows[-1]['deployment_url'], deployment()['url'])
        self.assertEqual(rows[-1]['alias_url'], d.ALIAS)
        self.assertEqual(next(x for x in rows if x['stage'] == 'production_state')['historical_production_unchanged'], 'not_assessed')

    def test_second_page_and_later_duplicate_marker(self):
        first = self.page(10)
        rc, rows, calls = self.execute([Response(project()), Response([]), Response(first), Response([deployment()])])
        self.assertEqual(rc, 0)
        self.assertTrue(calls[-1].full_url.endswith('env=preview&per_page=10&page=2'))
        first[0]['deployment_trigger']['metadata']['commit_message'] = d.MARKER
        rc, rows, _ = self.execute([Response(project()), Response([]), Response(first), Response([deployment()])])
        self.assertEqual(rc, 1)
        self.assertEqual(rows[-1]['marker_state'], 'ambiguous')
        self.assertNotIn('deployment_url', rows[-1])

    def test_pagination_bound_and_repeated_ids_fail_closed(self):
        pages = [Response(self.page(10 + n * 10)) for n in range(d.MAX_PAGES)]
        rc, rows, calls = self.execute([Response(project()), Response([])] + pages)
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 2 + d.MAX_PAGES)
        self.assertTrue(any(x['stage'] == 'preview_pagination_limit' for x in rows))
        self.assertFalse(any(x.get('marker_state') == 'not_found' for x in rows))
        rc, rows, calls = self.execute([Response(project()), Response([]), Response(self.page(10)), Response(self.page(10))])
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 4)

    def test_production_pagination_and_present_state(self):
        prod = self.page(10)
        for item in prod:
            item['environment'] = 'production'
        pr = project(); pr['canonical_deployment'] = prod[0]
        rc, rows, calls = self.execute([Response(pr), Response(prod), Response([]), Response([deployment()])])
        self.assertEqual(rc, 1)
        self.assertTrue(calls[2].full_url.endswith('env=production&per_page=10&page=2'))
        state = next(x for x in rows if x['stage'] == 'production_state')
        self.assertFalse(state['current_production_empty'])
        self.assertTrue(state['canonical_consistent'])
        self.assertEqual(state['production_count'], 10)

    def test_pending_failed_alias_and_url_do_not_verify(self):
        for field, value, state in (
            ('aliases', [], 'alias_missing_or_wrong'),
            ('aliases', ['https://evil.invalid/' + TOKEN], 'alias_missing_or_wrong'),
            ('url', 'https://1234abcd.' + d.PROJECT + '.pages.dev.evil.invalid', 'url_rejected'),
            ('latest_stage', {'name': 'deploy', 'status': 'active'}, 'pending'),
            ('latest_stage', {'name': 'deploy', 'status': 'failure'}, 'deploy_failed'),
        ):
            item = deployment(); item[field] = value
            rc, rows, _ = self.execute([Response(project()), Response([]), Response([item])])
            self.assertEqual(rc, 1)
            self.assertEqual(rows[-1]['marker_state'], state)
            self.assertNotIn('deployment_url', rows[-1])

    def test_bad_project_and_malformed_list_stop(self):
        for field, value in (('name', TOKEN), ('production_branch', d.BRANCH),
                             ('production_branch', ''), ('canonical_deployment', 'missing'),
                             ('deployment_configs', None), ('source', {'type': 'github'})):
            pr = project(); pr[field] = value
            rc, _, calls = self.execute([Response(pr)])
            self.assertEqual(rc, 1); self.assertEqual(len(calls), 1)
        for batch in ([dict(deployment(), id=TOKEN)], self.page(10, 11), [dict(deployment(), deployment_trigger=None)]):
            rc, rows, _ = self.execute([Response(project()), Response([]), Response(batch)])
            self.assertEqual(rc, 1)
            self.assertNotIn('deployment_url', rows[-1])

    def test_no_main_context_no_network(self):
        with patch.dict(os.environ,{},clear=True),patch('urllib.request.build_opener') as build,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(d.main(),1);build.assert_not_called()
    def test_no_deploy_import_or_process_execution(self):
        import ast
        from pathlib import Path
        tree=ast.parse(Path(d.__file__).read_text())
        imports=[n.names[0].name for n in ast.walk(tree) if isinstance(n,ast.Import)]
        self.assertNotIn('publisher',imports);self.assertNotIn('subprocess',imports)

if __name__ == '__main__': unittest.main()
