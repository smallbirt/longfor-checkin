"""Import guidance must distinguish parser/readback failures without echoing secrets."""
import copy
import io
import json
import unittest
from app.importer import parse_capture
from app.import_diagnostics import capture_failure_hint, validation_failure_hint
from test_login import login_entry, har, TOKEN, PHONE, PROFILE
import test_login
from test_core import credential

SECRET='private-server-message-token-otp-cookie'


class CaptureDiagnosticTests(unittest.TestCase):
    def hint(self,entry):
        with self.assertRaises(ValueError) as context:parse_capture(har(entry))
        message=str(context.exception)
        for private in (SECRET,PHONE,TOKEN,PROFILE['fingerprint'],'123456'):
            self.assertNotIn(private,message)
        return message

    def test_missing_response_explains_export_content(self):
        entry=login_entry();entry['response']['content']['text']=''
        message=self.hint(entry)
        self.assertIn('响应正文',message)
        self.assertIn('App',message)

    def test_bad_response_is_not_echoed(self):
        entry=login_entry('mini');entry['response']['content']['text']=SECRET
        self.assertIn('无法解析',self.hint(entry))

    def test_failed_login_preserves_numeric_codes_only(self):
        entry=login_entry();entry['response']['content']['text']=json.dumps({'code':'801001','msg':SECRET+TOKEN})
        self.assertIn('801001',self.hint(entry))
        entry['response']['content']['text']=json.dumps({'code':SECRET+TOKEN,'msg':PHONE})
        self.assertNotIn(SECRET,self.hint(entry))

    def test_success_shaped_unsupported_format_needs_adapter_not_long_capture(self):
        entry=login_entry();entry['response']['content']['text']=json.dumps({'code':'200','data':{'token':'unsupported-token-format-'+SECRET}})
        self.assertIn('不匹配',self.hint(entry))

    def test_other_first_party_login_path_is_reported_without_echoing_url(self):
        entry=login_entry();entry['request']['url']=entry['request']['url'].replace('smsCodeSecret','otherLogin')+'?token='+SECRET
        self.assertIn('当前支持',self.hint(entry))
        entry['request']['url']=entry['request']['url'].replace('gw2c-hw-open.longfor.com','evil.example')
        self.assertIn('没有找到',self.hint(entry))

    def test_no_requests_and_malformed_entries_do_not_echo_data(self):
        self.assertIn('没有找到',capture_failure_hint([None,SECRET,{'request':{'url':SECRET}}]))


class ValidationDiagnosticTests(unittest.TestCase):
    def hint(self,state='unknown',diagnostic=None):
        result={'state':state,'message':SECRET+TOKEN,'diagnostics':[diagnostic or {}]}
        message=validation_failure_hint(credential('mini'),result)
        for private in (SECRET,TOKEN,PHONE,'private-login-token','private-device-token'):
            self.assertNotIn(private,message)
        self.assertIn('旧凭证已保留',message)
        return message

    def test_auth_failure_exposes_safe_code_and_channel(self):
        message=self.hint('auth_expired',{'reason':'business','business_code':'801001','http_status':200})
        self.assertIn('801001',message);self.assertIn('微信小程序',message)
        self.assertIn('已识别',message)

    def test_network_failure_does_not_tell_user_to_recapture(self):
        for reason in ('timeout','tls','connection','network'):
            self.assertIn('先检查',self.hint(diagnostic={'reason':reason}))

    def test_unknown_calendar_is_not_misreported_as_expired(self):
        message=self.hint()
        self.assertIn('未获得明确',message)
        self.assertNotIn('判定新登录失效',message)

    def test_untrusted_diagnostic_values_are_ignored(self):
        message=self.hint(diagnostic={'reason':[SECRET],'http_status':True,'business_code':TOKEN})
        self.assertNotIn('HTTP',message)
        self.assertNotIn('业务码',message)


class ImportDiagnosticRouteTests(unittest.TestCase):
    setUp=test_login.LoginRoutesTests.setUp
    tearDown=test_login.LoginRoutesTests.tearDown
    upload=test_login.LoginRoutesTests.upload

    def test_failed_validation_error_keeps_old_credential_and_displays_code(self):
        self.network.run.return_value={'state':'auth_expired','message':SECRET,
            'diagnostics':[{'reason':'business','http_status':200,'business_code':'801001'}]}
        response=self.upload(login_entry())
        self.assertEqual(response.status_code,400)
        self.assertIn('801001',response.json['error'])
        self.assertNotIn(SECRET,response.json['error'])
        self.assertEqual(self.store.get('credential:app'),credential())
        self.assertEqual(self.store.get('login-profile:app'),PROFILE)
        self.assertIs(self.network.run.call_args.kwargs['allow_sign'],False)

    def test_wrong_channel_is_explicit_and_never_queries(self):
        response=self.client.post('/api/import',data={'channel':'mini',
            'file':(io.BytesIO(har(login_entry())),'app.har')},headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertIn('文件识别到',response.json['error'])
        self.assertIn('当前选择的是',response.json['error'])
        self.network.run.assert_not_called()
        self.assertEqual(self.store.get('credential:app'),credential())
