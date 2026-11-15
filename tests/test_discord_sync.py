"""Behavioral tests against simulated Discord inventories, never a live server."""

import copy
import io
import json
import unittest
import urllib.error

from automation import discord_sync as sync


GUILD = "100000000000000001"
BOT = "100000000000000002"
BOT_ROLE = "100000000000000003"
STAFF = "100000000000000004"
CATEGORY = "100000000000000005"
CHANNEL = "100000000000000006"
OTHER_MEMBER = "100000000000000007"
OTHER_CHANNEL = "100000000000000008"
REZALET = "843996519354859541"
REZALET_ROLE = "100000000000000009"


def configuration():
    return {
        "guild_id": GUILD,
        "roles": [{"key": "staff", "name": "Moderação", "aliases": ["Moderator"],
                   "permissions": "0", "color": 0}],
        "categories": [{
            "key": "private", "name": "EQUIPE", "access": ["staff"],
            "channels": [{"key": "logs", "name": "registros", "type": 0,
                          "topic": "Registros privados", "write_roles": ["staff"]}],
        }],
        "announcements": {"coordination": {
            "channel": "logs", "content": "Agenda: @everyone <@100000000000000007>"}},
    }


def inventories():
    roles = [
        {"id": GUILD, "name": "@everyone", "permissions": "0", "position": 0},
        {"id": BOT_ROLE, "name": "Configuração", "permissions": str(
            ((1 << 54) - 1) & ~sync.ADMINISTRATOR), "position": 20, "managed": True},
        {"id": STAFF, "name": "Moderação", "permissions": "0", "color": 0,
         "position": 10, "managed": False},
    ]
    channels = [
        {"id": CATEGORY, "name": "EQUIPE", "type": 4,
         "permission_overwrites": []},
        {"id": CHANNEL, "name": "registros", "type": 0, "parent_id": CATEGORY,
         "topic": "Registros privados", "permission_overwrites": [
             {"id": OTHER_MEMBER, "type": 1, "allow": str(sync.VIEW_CHANNEL), "deny": "0"}]},
        {"id": OTHER_CHANNEL, "name": "ticket-em-andamento", "type": 0,
         "parent_id": None, "topic": "Não pertence à configuração",
         "permission_overwrites": [{"id": OTHER_MEMBER, "type": 1,
                                    "allow": str(sync.VIEW_CHANNEL), "deny": "0"}]},
    ]
    return roles, channels, {"id": BOT, "bot": True}, {"roles": [BOT_ROLE]}


class FakeDiscord:
    """Stateful fake: creates and patches resources, recording every HTTP verb."""

    def __init__(self, state=None):
        self.roles, self.channels, self.bot, self.member = copy.deepcopy(state or inventories())
        self.calls = []
        self.next_id = 100000000000001000

    def request(self, method, path, payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if method == "GET":
            resources = {
                "/users/@me": self.bot,
                f"/guilds/{GUILD}/roles": self.roles,
                f"/guilds/{GUILD}/channels": self.channels,
                f"/guilds/{GUILD}/members/{BOT}": self.member,
            }
            return copy.deepcopy(resources[path])
        if method == "POST":
            self.next_id += 1
            value = {"id": str(self.next_id), **copy.deepcopy(payload)}
            if path == f"/guilds/{GUILD}/roles":
                value.update(position=1, managed=False)
                self.roles.append(value)
            elif path == f"/guilds/{GUILD}/channels":
                self.channels.append(value)
            elif not path.endswith("/messages"):
                raise AssertionError(f"Unexpected creation target: {path}")
            return copy.deepcopy(value)
        if method == "PATCH":
            resources = self.roles if "/roles/" in path else self.channels
            target = next(item for item in resources if item["id"] == path.rsplit("/", 1)[-1])
            target.update(copy.deepcopy(payload))
            return copy.deepcopy(target)
        raise AssertionError(f"Unexpected HTTP method: {method}")

    @property
    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]


class ReconciliationTests(unittest.TestCase):
    def test_plan_is_read_only_and_does_not_mutate_inventories(self):
        client = FakeDiscord()
        before = copy.deepcopy((client.roles, client.channels))
        plan = sync.reconcile(configuration(), client)
        self.assertGreater(plan["summary"]["changes"], 0)
        self.assertEqual([], client.writes)
        self.assertEqual(before, (client.roles, client.channels))

    def test_apply_reads_back_and_second_application_is_idempotent(self):
        client = FakeDiscord()
        unrelated_before = copy.deepcopy(client.channels[-1])
        first = sync.reconcile(configuration(), client, apply=True)
        self.assertTrue(first["verified_configuration"])
        self.assertEqual([], first["remaining_operations"])
        self.assertEqual(unrelated_before, client.channels[-1])
        self.assertTrue(all(method in {"GET", "POST", "PATCH"}
                            for method, _, _ in client.calls))
        client.calls.clear()
        second = sync.reconcile(configuration(), client, apply=True)
        self.assertEqual(0, second["summary"]["completed"])
        self.assertEqual([], client.writes)

    def test_create_resolves_new_role_and_category_ids_before_channel_creation(self):
        roles, channels, bot, member = inventories()
        client = FakeDiscord((roles[:2], [channels[-1]], bot, member))
        result = sync.reconcile(configuration(), client, apply=True)
        self.assertTrue(result["verified_configuration"])
        created_category = next(item for item in client.channels if item["type"] == 4)
        created_channel = next(item for item in client.channels if item["name"] == "registros")
        self.assertEqual(created_category["id"], created_channel["parent_id"])
        self.assertNotIn("$role:", json.dumps(client.channels))
        self.assertNotIn("$category:", json.dumps(client.channels))

    def test_private_acl_removes_stale_person_exception_and_denies_everyone(self):
        roles, channels, bot, member = inventories()
        plan = sync.build_plan(configuration(), roles, channels, bot, member)
        update = next(item for item in plan["operations"] if item["key"] == "logs")
        overwrites = update["payload"]["permission_overwrites"]
        self.assertNotIn(OTHER_MEMBER, {row["id"] for row in overwrites})
        everyone = next(row for row in overwrites if row["id"] == GUILD)
        self.assertTrue(int(everyone["deny"]) & sync.VIEW_CHANNEL)
        staff = next(row for row in overwrites if row["id"] == STAFF)
        self.assertTrue(int(staff["allow"]) & sync.VIEW_CHANNEL)
        bot_exception = next(row for row in overwrites if row["id"] == BOT)
        self.assertEqual(1, bot_exception["type"])
        self.assertTrue(int(bot_exception["allow"]) & sync.SEND_MESSAGES)

    def test_read_only_channel_blocks_messages_and_threads_for_everyone(self):
        config = configuration()
        config["categories"][0]["access"] = ["@everyone"]
        plan = sync.build_plan(config, *inventories())
        update = next(item for item in plan["operations"] if item["key"] == "logs")
        rows = update["payload"]["permission_overwrites"]
        everyone = next(row for row in rows if row["id"] == GUILD)
        for flag in (sync.SEND_MESSAGES, sync.CREATE_PUBLIC_THREADS,
                     sync.CREATE_PRIVATE_THREADS, sync.SEND_MESSAGES_IN_THREADS):
            self.assertTrue(int(everyone["deny"]) & flag)
            self.assertFalse(int(everyone["allow"]) & flag)

    def test_ambiguous_role_name_aborts_apply_before_any_write(self):
        client = FakeDiscord()
        client.roles.append({**client.roles[-1], "id": OTHER_MEMBER, "name": "Moderator"})
        with self.assertRaises(sync.SyncError):
            sync.reconcile(configuration(), client, apply=True)
        self.assertEqual([], client.writes)

    def test_ambiguous_channel_aborts_apply_before_any_write(self):
        client = FakeDiscord()
        client.channels.append({**client.channels[1], "id": OTHER_MEMBER})
        with self.assertRaises(sync.SyncError):
            sync.reconcile(configuration(), client, apply=True)
        self.assertEqual([], client.writes)

    def test_news_channel_is_updated_without_converting_or_recreating_it(self):
        client = FakeDiscord()
        client.channels[1]["type"] = 5
        result = sync.reconcile(configuration(), client, apply=True)
        self.assertTrue(result["verified_configuration"])
        self.assertEqual(5, client.channels[1]["type"])
        self.assertFalse(any(method == "POST" and path.endswith("/channels")
                             for method, path, _ in client.calls))

    def test_role_at_bot_height_is_blocked_before_any_resource_changes(self):
        client = FakeDiscord()
        client.roles[-1].update(position=20, color=123)
        plan = sync.reconcile(configuration(), client)
        self.assertEqual(1, plan["summary"]["blocked"])
        with self.assertRaises(sync.SyncError):
            sync.reconcile(configuration(), client, apply=True)
        self.assertEqual([], client.writes)

    def test_matching_managed_role_is_preserved_and_configuration_rejected(self):
        client = FakeDiscord()
        client.roles[-1]["managed"] = True
        with self.assertRaises(sync.SyncError):
            sync.reconcile(configuration(), client, apply=True)
        self.assertEqual([], client.writes)

    def test_missing_manage_roles_aborts_before_other_writes(self):
        client = FakeDiscord()
        client.roles[1]["permissions"] = str(sync.MANAGE_CHANNELS)
        client.roles[-1]["color"] = 123
        with self.assertRaises(sync.SyncError):
            sync.reconcile(configuration(), client, apply=True)
        self.assertEqual([], client.writes)

    def test_acl_edit_requires_manage_roles_even_when_no_role_edit_is_needed(self):
        client = FakeDiscord()
        client.roles[1]["permissions"] = str(((1 << 54) - 1)
                                             & ~sync.ADMINISTRATOR & ~sync.MANAGE_ROLES)
        plan = sync.reconcile(configuration(), client)
        self.assertTrue(plan["summary"]["blocked"])
        self.assertFalse(any(item["kind"].startswith("role.") for item in plan["operations"]))
        with self.assertRaises(sync.SyncError):
            sync.reconcile(configuration(), client, apply=True)
        self.assertEqual([], client.writes)

    def test_category_change_cannot_modify_synchronized_unmanaged_child(self):
        client = FakeDiscord()
        client.channels[-1].update(parent_id=CATEGORY, permission_overwrites=[])
        before = copy.deepcopy(client.channels[-1])
        plan = sync.reconcile(configuration(), client)
        category_change = next(item for item in plan["operations"] if item["key"] == "private")
        self.assertIn("blocked_reason", category_change)
        with self.assertRaises(sync.SyncError):
            sync.reconcile(configuration(), client, apply=True)
        self.assertEqual([], client.writes)
        self.assertEqual(before, client.channels[-1])

    def test_unresolved_integration_prevents_claiming_configuration_verified(self):
        config = configuration()
        config["integrations"] = [{"name": "Rezaletbot", "bot_id": REZALET,
                                   "channels": ["logs"]}]
        client = FakeDiscord()
        result = sync.reconcile(config, client, apply=True)
        self.assertEqual([], result["remaining_operations"])
        self.assertTrue(result["pending_manual"])
        self.assertFalse(result["verified_configuration"])

    def test_integration_access_is_limited_to_allowlisted_channel(self):
        config = configuration()
        config["categories"][0]["channels"].append({
            "key": "staff_chat", "name": "equipe", "type": 0})
        config["integrations"] = [{"name": "Rezaletbot", "bot_id": REZALET,
                                   "channels": ["logs"]}]
        roles, channels, bot, member = inventories()
        roles.append({"id": REZALET_ROLE, "name": "Rezaletbot", "position": 2,
                      "permissions": "0", "managed": True, "tags": {"bot_id": REZALET}})
        plan = sync.build_plan(config, roles, channels, bot, member)
        for operation in plan["operations"]:
            if not operation["kind"].startswith(("channel.", "category.")):
                continue
            rows = operation["payload"]["permission_overwrites"]
            exception = [row for row in rows if row["id"] == REZALET_ROLE]
            if operation["key"] == "logs":
                self.assertEqual(1, len(exception))
                self.assertEqual(0, exception[0]["type"])
                self.assertTrue(int(exception[0]["allow"]) & sync.VIEW_CHANNEL)
                self.assertTrue(int(exception[0]["allow"]) & sync.SEND_MESSAGES_IN_THREADS)
            else:
                self.assertEqual([], exception)
        self.assertTrue(plan["integrations"][0]["present"])

    def test_integration_name_or_unmanaged_role_cannot_spoof_bot_identity(self):
        config = configuration()
        config["integrations"] = [{"name": "Rezaletbot", "bot_id": REZALET,
                                   "channels": ["logs"]}]
        roles, channels, bot, member = inventories()
        roles.append({"id": REZALET_ROLE, "name": "Rezaletbot", "position": 2,
                      "permissions": "0", "managed": False,
                      "tags": {"bot_id": REZALET}})
        plan = sync.build_plan(config, roles, channels, bot, member)
        self.assertFalse(plan["integrations"][0]["present"])
        self.assertTrue(plan["pending_manual"])
        serialized_acl = json.dumps([operation["payload"].get("permission_overwrites", [])
                                     for operation in plan["operations"]])
        self.assertNotIn(REZALET_ROLE, serialized_acl)


class ValidationTests(unittest.TestCase):
    def test_administrator_cannot_be_granted(self):
        config = configuration()
        config["roles"][0]["permissions"] = str(sync.ADMINISTRATOR)
        with self.assertRaises(sync.SyncError):
            sync.validate_config(config)

    def test_private_writer_must_be_allowed_to_view_category(self):
        config = configuration()
        config["roles"].append({"key": "community", "name": "Comunidade", "permissions": "0"})
        config["categories"][0]["channels"][0]["write_roles"] = ["community"]
        with self.assertRaises(sync.SyncError):
            sync.validate_config(config)

    def test_duplicate_names_and_unknown_role_references_are_rejected(self):
        config = configuration()
        config["roles"].append({"key": "other", "name": "Moderator", "permissions": "0"})
        with self.assertRaises(sync.SyncError):
            sync.validate_config(config)

    def test_integration_cannot_target_unmanaged_channels(self):
        config = configuration()
        config["integrations"] = [{"name": "Rezaletbot", "bot_id": REZALET,
                                   "channels": ["unmanaged_ticket"]}]
        with self.assertRaises(sync.SyncError):
            sync.validate_config(config)
        config = configuration()
        config["categories"][0]["access"] = ["missing"]
        with self.assertRaises(sync.SyncError):
            sync.validate_config(config)


class AnnouncementAndTransportTests(unittest.TestCase):
    def test_sensitive_everyone_permissions_block_announcements_until_review(self):
        client = FakeDiscord()
        sync.reconcile(configuration(), client, apply=True)
        client.roles[0]["permissions"] = str(sync.MANAGE_GUILD)
        client.calls.clear()
        with self.assertRaises(sync.SyncError):
            sync.announce(configuration(), client, "coordination")
        self.assertEqual([], client.writes)

    def test_announcement_cannot_publish_before_acl_reconciliation(self):
        client = FakeDiscord()
        with self.assertRaises(sync.SyncError):
            sync.announce(configuration(), client, "coordination")
        self.assertEqual([], client.writes)

    def test_announcement_disables_all_mentions_and_does_not_return_message_body(self):
        client = FakeDiscord()
        sync.reconcile(configuration(), client, apply=True)
        client.calls.clear()
        result = sync.announce(configuration(), client, "coordination")
        self.assertEqual(1, len(client.writes))
        method, path, payload = client.writes[0]
        self.assertEqual("POST", method)
        self.assertEqual(f"/channels/{CHANNEL}/messages", path)
        self.assertEqual([], payload["allowed_mentions"]["parse"])
        self.assertFalse(payload["allowed_mentions"]["replied_user"])
        self.assertNotIn("content", result)
        self.assertNotIn("Agenda", json.dumps(result))

    def test_unknown_announcement_cannot_send_message(self):
        client = FakeDiscord()
        with self.assertRaises(sync.SyncError):
            sync.announce(configuration(), client, "arbitrary")
        self.assertEqual([], client.calls)

    def test_transport_refuses_deletion_and_arbitrary_urls_without_network(self):
        def fail_if_called(*args, **kwargs):
            self.fail("Invalid requests must not reach the HTTP transport")
        client = sync.DiscordClient("secret-not-to-be-printed", opener=fail_if_called)
        for method, path in [("DELETE", f"/channels/{CHANNEL}"),
                             ("GET", "https://example.com/"),
                             ("GET", "/../users")]:
            with self.assertRaises(sync.SyncError):
                client.request(method, path)

    def test_post_gateway_failure_not_retried_or_leaked(self):
        requests = []

        def fail(request, **kwargs):
            requests.append(request)
            raise urllib.error.HTTPError(request.full_url, 502, "Bad gateway", {},
                                         io.BytesIO(b"secret-not-to-be-printed"))

        client = sync.DiscordClient("secret-not-to-be-printed", opener=fail,
                                    sleeper=lambda delay: self.fail("POST must not retry"))
        with self.assertRaises(sync.SyncError) as raised:
            client.request("POST", f"/channels/{CHANNEL}/messages", {"content": "hello"})
        self.assertEqual(1, len(requests))
        self.assertNotIn("secret-not-to-be-printed", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
