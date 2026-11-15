import copy
import json
import unittest

from automation.inspect_discord import inspect


class InspectionTests(unittest.TestCase):
    def test_read_only_inspection_exposes_candidates_without_private_overwrites(self):
        guild, bot, bot_role = '1571058974625628182', '1571313716090249371', '1571313716090249372'
        config = {'guild_id': guild, 'roles': [], 'categories': [{
            'key': 'information', 'name': 'INFORMAÇÕES', 'access': ['@everyone'], 'channels': [
                {'key': 'rules', 'name': 'boas-vindas-e-regras', 'aliases': ['regras'], 'type': 0}]}]}
        channels = [
            {'id': '1571058975439331359', 'name': 'boas-vindas-e-regras', 'type': 0,
             'parent_id': None, 'permission_overwrites': [{'id': 'private-member-id', 'type': 1}]},
            {'id': '1571058975439331360', 'name': 'regras', 'type': 0, 'parent_id': None},
            {'id': '1571058975439331361', 'name': 'unknown-private-channel-name', 'type': 0},
        ]
        original = copy.deepcopy(channels)
        calls = []

        class Fake:
            def request(self, method, path, payload=None):
                calls.append((method, path))
                return {
                    '/users/@me': {'id': bot},
                    f'/guilds/{guild}/roles': [{'id': guild, 'name': '@everyone', 'permissions': '0'},
                                              {'id': bot_role, 'name': 'configuration-bot', 'position': 3}],
                    f'/guilds/{guild}/channels': channels,
                    f'/guilds/{guild}/members/{bot}': {'roles': [bot_role]},
                }[path]

        report = inspect(config, Fake())
        self.assertEqual(len(report['channels']['rules']), 2)
        self.assertTrue(all(method == 'GET' for method, _ in calls))
        serialized = json.dumps(report)
        self.assertNotIn('private-member-id', serialized)
        self.assertNotIn('unknown-private-channel-name', serialized)
        self.assertEqual(channels, original)


if __name__ == '__main__':
    unittest.main()
