import os
from waitress import serve
from . import create_app

if __name__ == '__main__':
    app = create_app(worker=True)
    serve(app, host=os.environ.get('LISTEN_HOST', '0.0.0.0'), port=int(os.environ.get('LISTEN_PORT', '8080')),
          threads=6, max_request_body_size=50 * 1024 * 1024)
