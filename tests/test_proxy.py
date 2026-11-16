import json
import unittest
from werkzeug.test import Client
from werkzeug.wrappers import Response
from app.proxy import TrustedProxy


class TrustedProxyTests(unittest.TestCase):
    def client(self, addresses='172.19.0.254'):
        def application(environ, start_response):
            return Response(json.dumps({'ip': environ['REMOTE_ADDR'],
                'scheme': environ['wsgi.url_scheme'], 'host': environ['HTTP_HOST']}),
                content_type='application/json')(environ, start_response)
        return Client(TrustedProxy(application, addresses), Response)

    def request(self, address, addresses='172.19.0.254'):
        return self.client(addresses).get('/', environ_overrides={'REMOTE_ADDR': address},
            headers={'Host': 'fitlean-api.mingyuefanxing.top',
                     'X-Forwarded-For': '198.51.100.5', 'X-Forwarded-Proto': 'https',
                     'X-Forwarded-Host': 'untrusted.example'}).json

    def test_proxy_preserves_client_ip_and_https_origin(self):
        self.assertEqual(self.request('172.19.0.254'), {'ip': '198.51.100.5',
            'scheme': 'https', 'host': 'fitlean-api.mingyuefanxing.top'})

    def test_direct_requests_cannot_spoof_forwarding_headers(self):
        self.assertEqual(self.request('203.0.113.5'), {'ip': '203.0.113.5',
            'scheme': 'http', 'host': 'fitlean-api.mingyuefanxing.top'})

    def test_default_trusts_no_proxy(self):
        self.assertEqual(self.request('172.19.0.254', '')['scheme'], 'http')

    def test_invalid_address_fails_closed(self):
        with self.assertRaises(ValueError):self.client('0.0.0.0/0')
