"""Accept forwarding headers only from explicitly configured proxy addresses."""
import ipaddress
from werkzeug.middleware.proxy_fix import ProxyFix


class TrustedProxy:
    def __init__(self, app, addresses):
        self.app = app
        self.addresses = {str(ipaddress.ip_address(value.strip()))
                          for value in addresses.split(',') if value.strip()}
        self.forwarded = ProxyFix(app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)

    def __call__(self, environ, start_response):
        if environ.get('REMOTE_ADDR') in self.addresses:
            return self.forwarded(environ, start_response)
        return self.app(environ, start_response)
