"""FineFlow SMS to WhatsApp pilot. Python 3.11+, standard library only."""
import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = os.environ.get('FINEFLOW_DB', str(ROOT / 'fineflow.sqlite3'))
CONFIG = os.environ.get('FINEFLOW_CONFIG', str(ROOT / 'devices.json'))


def config():
    return json.loads(Path(CONFIG).read_text())


def database():
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('''CREATE TABLE IF NOT EXISTS notices (
        device TEXT NOT NULL, event_id TEXT NOT NULL, sender TEXT NOT NULL,
        received_at INTEGER NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL,
        provider_id TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        PRIMARY KEY(device,event_id))''')
    con.commit()
    return con


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(devices, token):
    if not token:
        return None
    hashed = digest(token)
    for name, settings in devices.items():
        if hmac.compare_digest(settings.get('token_sha256', ''), hashed):
            return name, settings
    return None


def matches(settings, sender, body):
    allowed = {s.strip().casefold() for s in settings.get('senders', []) if s.strip()}
    # Explicit sender allowlist AND one configured keyword. No match-all defaults.
    words = [s.strip().casefold() for s in settings.get('keywords', []) if s.strip()]
    return (sender.strip().casefold() in allowed and bool(words)
            and any(w in body.casefold() for w in words))


def validate_event(event):
    if not isinstance(event, dict):
        raise ValueError('JSON object required')
    for field, maximum in [('event_id', 100), ('sender', 100), ('body', 3000)]:
        if not isinstance(event.get(field), str) or not 1 <= len(event[field]) <= maximum:
            raise ValueError('Invalid ' + field)
    if not re.fullmatch(r'[A-Za-z0-9_.:-]+', event['event_id']):
        raise ValueError('Invalid event_id')
    timestamp = event.get('received_at')
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        raise ValueError('Invalid received_at')
    if timestamp > int(time.time() * 1000) + 300000:
        raise ValueError('Timestamp is in the future')


def template_payload(settings, event):
    return {
        'messaging_product': 'whatsapp', 'to': settings['admin_phone'], 'type': 'template',
        'template': {'name': os.environ.get('WA_TEMPLATE', 'fineflow_notice'),
                     'language': {'code': os.environ.get('WA_LANGUAGE', 'en')},
                     'components': [{'type': 'body', 'parameters': [
                         {'type': 'text', 'text': settings.get('owner_label', 'Vehicle owner')},
                         {'type': 'text', 'text': event['sender']},
                         {'type': 'text', 'text': ' '.join(event['body'].split())},
                         {'type': 'text', 'text': event['event_id']}
                     ]}]}}


def send_whatsapp(settings, event):
    mode = os.environ.get('FINEFLOW_MODE', 'test')
    if mode == 'test':
        return 'simulated', None
    if mode != 'live':
        raise ValueError('FINEFLOW_MODE must be test or live')
    required = ['WA_ACCESS_TOKEN', 'WA_PHONE_NUMBER_ID', 'WA_GRAPH_VERSION']
    if any(not os.environ.get(k) for k in required):
        raise ValueError('Missing WhatsApp server configuration')
    if not settings.get('admin_opt_in'):
        raise ValueError('Administrator opt-in not recorded')
    if not re.fullmatch(r'[1-9][0-9]{7,14}', settings.get('admin_phone', '')):
        raise ValueError('Invalid administrator phone')
    if not re.fullmatch(r'v[0-9]+\.[0-9]+', os.environ['WA_GRAPH_VERSION']):
        raise ValueError('Invalid Graph version')
    if not os.environ['WA_PHONE_NUMBER_ID'].isdigit():
        raise ValueError('Invalid WhatsApp phone number ID')
    url = ('https://graph.facebook.com/' + os.environ['WA_GRAPH_VERSION'] + '/'
           + os.environ['WA_PHONE_NUMBER_ID'] + '/messages')
    request = urllib.request.Request(url, data=json.dumps(template_payload(settings, event)).encode(),
        headers={'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + os.environ['WA_ACCESS_TOKEN']}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
            return 'accepted', result['messages'][0]['id']
    except urllib.error.HTTPError:
        # Do not expose provider error bodies or tokens to the phone/logs.
        return 'rejected', None
    except (OSError, ValueError, KeyError, IndexError):
        # A timeout may happen after Meta accepted the message. Never blindly resend.
        return 'uncertain', None


def ingest(device, settings, event, send=send_whatsapp):
    validate_event(event)
    if not matches(settings, event['sender'], event['body']):
        return {'status': 'filtered'}
    now = int(time.time())
    with database() as con:
        try:
            con.execute('INSERT INTO notices VALUES (?,?,?,?,?,?,?,?,?)',
                        (device, event['event_id'], event['sender'], event['received_at'],
                         event['body'], 'processing', None, now, now))
        except sqlite3.IntegrityError:
            row = con.execute('SELECT status FROM notices WHERE device=? AND event_id=?',
                              (device, event['event_id'])).fetchone()
            return {'status': row['status'], 'duplicate': True}
    try:
        status, provider_id = send(settings, event)
    except ValueError:
        status, provider_id = 'configuration_error', None
    except Exception:
        status, provider_id = 'uncertain', None
    with database() as con:
        con.execute('UPDATE notices SET status=?, provider_id=?, updated_at=? WHERE device=? AND event_id=?',
                    (status, provider_id, int(time.time()), device, event['event_id']))
    return {'status': status, 'duplicate': False}


def apply_webhook(raw):
    data = json.loads(raw)
    ranks = {'accepted': 0, 'sent': 1, 'delivered': 2, 'read': 3}
    with database() as con:
        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                for item in change.get('value', {}).get('statuses', []):
                    status = item.get('status')
                    if status not in ('sent', 'delivered', 'read', 'failed'):
                        continue
                    row = con.execute('SELECT status FROM notices WHERE provider_id=?', (item.get('id'),)).fetchone()
                    if row and (ranks.get(status, -1) >= ranks.get(row['status'], -1)
                                or (status == 'failed' and row['status'] not in ('read', 'delivered'))):
                        con.execute('UPDATE notices SET status=?, updated_at=? WHERE provider_id=?',
                                    (status, int(time.time()), item['id']))


class Handler(BaseHTTPRequestHandler):
    server_version = 'FineFlow/0.1'

    def log_message(self, fmt, *args):
        pass  # No message bodies, bearer tokens or personal data in access logs.

    def reply(self, code, value):
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/health':
            return self.reply(200, {'ok': True, 'mode': os.environ.get('FINEFLOW_MODE', 'test')})
        if parsed.path == '/webhook':
            query = urllib.parse.parse_qs(parsed.query)
            expected = os.environ.get('WA_VERIFY_TOKEN', '')
            actual = query.get('hub.verify_token', [''])[0]
            if expected and hmac.compare_digest(expected, actual) and query.get('hub.mode') == ['subscribe']:
                challenge = query.get('hub.challenge', [''])[0].encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', str(len(challenge)))
                self.end_headers()
                return self.wfile.write(challenge)
            return self.reply(403, {'error': 'Verification failed'})
        return self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16000:
                return self.reply(413, {'error': 'Invalid request size'})
            raw = self.rfile.read(length)
            if self.path == '/webhook':
                secret = os.environ.get('WA_APP_SECRET', '')
                signature = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
                if not secret or not hmac.compare_digest(signature, self.headers.get('X-Hub-Signature-256', '')):
                    return self.reply(403, {'error': 'Invalid signature'})
                apply_webhook(raw)
                return self.reply(200, {'ok': True})
            if self.path != '/v1/notices':
                return self.reply(404, {'error': 'Not found'})
            auth = self.headers.get('Authorization', '')
            identity = authenticate(config(), auth[7:] if auth.startswith('Bearer ') else '')
            if not identity:
                return self.reply(401, {'error': 'Invalid device token'})
            result = ingest(*identity, json.loads(raw))
            return self.reply(200, result)
        except (ValueError, UnicodeError):
            return self.reply(400, {'error': 'Invalid request'})
        except Exception:
            return self.reply(503, {'error': 'Service unavailable'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['serve', 'add-device', 'status', 'purge'])
    parser.add_argument('--device')
    parser.add_argument('--owner')
    parser.add_argument('--admin')
    parser.add_argument('--sender', action='append', default=[])
    parser.add_argument('--keyword', action='append', default=[])
    parser.add_argument('--admin-opt-in', action='store_true')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    if args.command == 'add-device':
        if not args.device or not args.owner or not re.fullmatch(r'[1-9][0-9]{7,14}', args.admin or ''):
            parser.error('Provide --device, --owner and --admin in international digits format')
        if not args.sender or not args.keyword or not args.admin_opt_in:
            parser.error('Provide sender/keyword filters and record administrator opt-in first')
        devices = config() if Path(CONFIG).exists() else {}
        if args.device in devices:
            parser.error('Device already exists; edit configuration or revoke it first')
        token = secrets.token_urlsafe(32)
        devices[args.device] = {'token_sha256': digest(token), 'owner_label': args.owner,
            'admin_phone': args.admin, 'admin_opt_in': True, 'senders': args.sender, 'keywords': args.keyword}
        fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(devices, out, indent=2)
        print('Device token (shown once): ' + token)
    elif args.command == 'status':
        with database() as con:
            for row in con.execute('SELECT device,event_id,status,updated_at FROM notices ORDER BY created_at DESC LIMIT 100'):
                print(json.dumps(dict(row)))
    elif args.command == 'purge':
        if args.days < 1:
            parser.error('--days must be at least 1')
        with database() as con:
            con.execute('PRAGMA secure_delete=ON')
            con.execute('DELETE FROM notices WHERE created_at < ?', (int(time.time()) - args.days * 86400,))
        print('Retention purge complete')
    else:
        config()
        database().close()
        ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
