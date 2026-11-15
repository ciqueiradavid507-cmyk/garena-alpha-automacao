import io
import json
import unittest
import urllib.error

from automation.discord_sync import DiscordClient, SyncError


class SafeApiDiagnosticsTests(unittest.TestCase):
    def test_unknown_guild_identifies_endpoint_and_integer_code_without_body(self):
        secret = 'must-never-appear-in-output'
        path = '/guilds/1571058974625628182/roles'
        body = json.dumps({'code': 10004, 'message': secret}).encode()

        def fail(request, **kwargs):
            raise urllib.error.HTTPError(request.full_url, 404, 'Not found', {}, io.BytesIO(body))

        with self.assertRaises(SyncError) as raised:
            DiscordClient(secret, opener=fail).request('GET', path)
        diagnostic = str(raised.exception)
        self.assertIn('404', diagnostic)
        self.assertIn(path, diagnostic)
        self.assertIn('10004', diagnostic)
        self.assertNotIn(secret, diagnostic)

    def test_noninteger_discord_code_is_not_echoed(self):
        secret = 'must-never-appear-in-output'
        body = json.dumps({'code': secret, 'message': secret}).encode()

        def fail(request, **kwargs):
            raise urllib.error.HTTPError(request.full_url, 404, 'Not found', {}, io.BytesIO(body))

        with self.assertRaises(SyncError) as raised:
            DiscordClient(secret, opener=fail).request('GET', '/users/@me')
        self.assertIn('/users/@me', str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))


if __name__ == '__main__':
    unittest.main()
