import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from automation import discord_sync as sync
from automation.invite_url import BOT_PERMISSIONS, GUILD_ID, invitation


class DeploymentConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((Path(__file__).parents[1] / 'config/server.json').read_text())

    def test_invitation_is_for_target_guild_without_administrator(self):
        url = urlsplit(invitation('1412051464796241971'))
        params = parse_qs(url.query)
        self.assertEqual(url.netloc, 'discord.com')
        self.assertEqual(params['guild_id'], [GUILD_ID])
        self.assertEqual(params['scope'], ['bot'])
        self.assertEqual(params['disable_guild_select'], ['true'])
        self.assertFalse(BOT_PERMISSIONS & sync.ADMINISTRATOR)
        self.assertFalse(BOT_PERMISSIONS & ((1 << 1) | (1 << 2) | sync.MANAGE_WEBHOOKS))

    def test_invalid_application_id_cannot_change_oauth_parameters(self):
        for value in ('', 'https://example.org', '123&scope=identify', '-1412051464796241971'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                invitation(value)

    def test_sixteen_digit_application_id_is_preserved_in_invitation(self):
        application_id = '8866461766385655'
        params = parse_qs(urlsplit(invitation(application_id)).query)
        self.assertEqual(params['client_id'], [application_id])

    def test_application_id_must_be_a_nonzero_unsigned_64_bit_integer(self):
        for value in ('0', '0000000000000000', str(2**64), '99999999999999999999'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                invitation(value)
        for value in ('1', str(2**64 - 1)):
            with self.subTest(value=value):
                params = parse_qs(urlsplit(invitation(value)).query)
                self.assertEqual(params['client_id'], [value])

    def test_bot_can_grant_declared_role_and_channel_permissions(self):
        required = sync.PARTICIPATE | sync.BOT_CHANNEL_ACCESS | sync.INTEGRATION_CHANNEL_ACCESS
        for role in self.config['roles']:
            required |= int(role['permissions'])
        self.assertEqual(required & ~BOT_PERMISSIONS, 0,
                         'O convite deve cobrir os poderes que o configurador concede.')

    def test_game_roles_never_grant_technical_management(self):
        forbidden = (sync.ADMINISTRATOR | sync.MANAGE_ROLES | sync.MANAGE_GUILD
                     | sync.MANAGE_CHANNELS | sync.MANAGE_WEBHOOKS | sync.MODERATE_MEMBERS)
        for role in self.config['roles']:
            if role['key'] not in ('administracao', 'moderacao'):
                self.assertFalse(int(role['permissions']) & forbidden, role['key'])

    def test_private_areas_and_integration_scope(self):
        categories = {item['key']: item for item in self.config['categories']}
        self.assertEqual(set(categories['equipe']['access']), {'administracao', 'moderacao'})
        self.assertNotIn('@everyone', categories['guilda']['access'])
        self.assertNotIn('recruta', categories['guilda']['access'])
        integration = self.config['integrations'][0]
        self.assertEqual(integration['bot_id'], '843996519354859541')
        self.assertNotIn('equipe_interna', integration['channels'])
        self.assertNotIn('coordenacao', integration['channels'])


if __name__ == '__main__':
    unittest.main()
