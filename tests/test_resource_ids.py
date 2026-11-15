"""Explicit resource IDs disambiguate inventories without replacing resources."""

import copy
import unittest

from automation import discord_sync as sync
from test_discord_sync import (
    BOT_ROLE, CATEGORY, CHANNEL, GUILD, OTHER_CHANNEL, OTHER_MEMBER, STAFF,
    FakeDiscord, configuration,
)


MISSING_ID = "100000000000999999"


class ExplicitResourceIdTests(unittest.TestCase):
    def test_channel_id_selects_only_intended_duplicate_inventory_name(self):
        config = configuration()
        config["categories"][0]["channels"][0]["id"] = CHANNEL
        client = FakeDiscord()
        duplicate = copy.deepcopy(client.channels[1])
        duplicate.update(id=OTHER_MEMBER, parent_id=None)
        client.channels.append(duplicate)
        duplicate_before = copy.deepcopy(duplicate)

        result = sync.reconcile(config, client, apply=True)

        self.assertTrue(result["verified_configuration"])
        self.assertEqual(duplicate_before, client.channels[-1])
        updated_channels = {path for method, path, _ in client.calls
                            if method == "PATCH" and path.startswith("/channels/")}
        self.assertIn(f"/channels/{CHANNEL}", updated_channels)
        self.assertNotIn(f"/channels/{OTHER_MEMBER}", updated_channels)

    def test_explicit_channel_id_renames_in_place_without_losing_messages(self):
        config = configuration()
        config["categories"][0]["channels"][0]["id"] = CHANNEL
        client = FakeDiscord()
        client.channels[1].update(name="canal-antigo", messages=["history-1", "history-2"])
        before_ids = [channel["id"] for channel in client.channels]

        result = sync.reconcile(config, client, apply=True)

        self.assertTrue(result["verified_configuration"])
        self.assertEqual(before_ids, [channel["id"] for channel in client.channels])
        selected = next(channel for channel in client.channels if channel["id"] == CHANNEL)
        self.assertEqual("registros", selected["name"])
        self.assertEqual(["history-1", "history-2"], selected["messages"])
        self.assertFalse(any(method in {"POST", "DELETE"} for method, _, _ in client.calls))

    def test_explicit_id_wins_over_name_and_alias_of_other_channel(self):
        config = configuration()
        target = config["categories"][0]["channels"][0]
        target.update(id=CHANNEL, aliases=["legado"])
        client = FakeDiscord()
        client.channels[1]["name"] = "nome-desatualizado"
        client.channels[-1]["name"] = "legado"
        unrelated_before = copy.deepcopy(client.channels[-1])

        sync.reconcile(config, client, apply=True)

        self.assertEqual("registros", client.channels[1]["name"])
        self.assertEqual(unrelated_before, client.channels[-1])

    def test_category_id_disambiguates_duplicate_category_names(self):
        config = configuration()
        config["categories"][0]["id"] = CATEGORY
        client = FakeDiscord()
        duplicate = copy.deepcopy(client.channels[0])
        duplicate["id"] = OTHER_MEMBER
        client.channels.append(duplicate)
        before = copy.deepcopy(duplicate)

        result = sync.reconcile(config, client, apply=True)

        self.assertTrue(result["verified_configuration"])
        self.assertEqual(before, client.channels[-1])

    def test_role_id_disambiguates_duplicate_alias_in_inventory(self):
        config = configuration()
        config["roles"][0]["id"] = STAFF
        client = FakeDiscord()
        duplicate = copy.deepcopy(client.roles[-1])
        duplicate.update(id=OTHER_MEMBER, name="Moderator", color=123, position=2)
        client.roles.append(duplicate)
        before = copy.deepcopy(duplicate)

        result = sync.reconcile(config, client, apply=True)

        self.assertTrue(result["verified_configuration"])
        self.assertEqual(before, client.roles[-1])
        self.assertFalse(any(method == "POST" and path.endswith("/roles")
                             for method, path, _ in client.calls))

    def test_missing_id_never_falls_back_to_name_or_creates_replacement(self):
        for resource_kind in ("role", "category", "channel"):
            with self.subTest(resource_kind=resource_kind):
                config = configuration()
                resources = {"role": config["roles"][0],
                             "category": config["categories"][0],
                             "channel": config["categories"][0]["channels"][0]}
                resources[resource_kind]["id"] = MISSING_ID
                client = FakeDiscord()
                with self.assertRaises(sync.SyncError):
                    sync.reconcile(config, client, apply=True)
                self.assertEqual([], client.writes)

    def test_managed_integration_role_cannot_be_modified_by_explicit_id(self):
        config = configuration()
        config["roles"][0]["id"] = STAFF
        client = FakeDiscord()
        client.roles[-1].update(name="Integração renomeada", managed=True)

        with self.assertRaises(sync.SyncError):
            sync.reconcile(config, client, apply=True)

        self.assertEqual([], client.writes)

    def test_explicit_role_id_cannot_bypass_bot_hierarchy(self):
        for position in (20, 21):
            with self.subTest(position=position):
                config = configuration()
                config["roles"][0]["id"] = STAFF
                client = FakeDiscord()
                client.roles[-1].update(position=position, color=123)
                plan = sync.reconcile(config, client)
                self.assertTrue(plan["summary"]["blocked"])
                with self.assertRaises(sync.SyncError):
                    sync.reconcile(config, client, apply=True)
                self.assertEqual([], client.writes)

    def test_explicit_channel_id_still_requires_compatible_type(self):
        config = configuration()
        config["categories"][0]["channels"][0]["id"] = CATEGORY
        client = FakeDiscord()
        with self.assertRaises(sync.SyncError):
            sync.reconcile(config, client, apply=True)
        self.assertEqual([], client.writes)


class ResourceIdValidationTests(unittest.TestCase):
    def test_invalid_id_values_are_rejected_for_each_resource_type(self):
        for resource_kind in ("role", "category", "channel"):
            for value in ("", "0", "not-an-id", "-123", str(2**64), 123, None):
                with self.subTest(resource_kind=resource_kind, value=value):
                    config = configuration()
                    resources = {"role": config["roles"][0],
                                 "category": config["categories"][0],
                                 "channel": config["categories"][0]["channels"][0]}
                    resources[resource_kind]["id"] = value
                    with self.assertRaises(sync.SyncError):
                        sync.validate_config(config)

    def test_duplicate_role_ids_are_rejected_even_with_different_names(self):
        config = configuration()
        config["roles"][0]["id"] = STAFF
        config["roles"].append({"key": "other_role", "name": "Outro cargo",
                                "permissions": "0", "id": STAFF})
        with self.assertRaises(sync.SyncError):
            sync.validate_config(config)

    def test_category_and_channel_share_id_namespace(self):
        config = configuration()
        config["categories"][0]["id"] = CHANNEL
        config["categories"][0]["channels"][0]["id"] = CHANNEL
        with self.assertRaises(sync.SyncError):
            sync.validate_config(config)

    def test_duplicate_channel_ids_are_rejected_even_with_different_names(self):
        config = configuration()
        config["categories"][0]["channels"][0]["id"] = CHANNEL
        config["categories"][0]["channels"].append({
            "key": "other_channel", "name": "outro-canal", "type": 0, "id": CHANNEL})
        with self.assertRaises(sync.SyncError):
            sync.validate_config(config)

    def test_role_and_channel_ids_have_separate_validation_namespaces(self):
        config = configuration()
        config["roles"][0]["id"] = CHANNEL
        config["categories"][0]["channels"][0]["id"] = CHANNEL
        sync.validate_config(config)

    def test_everyone_role_cannot_be_targeted_under_another_name(self):
        config = configuration()
        config["roles"][0]["id"] = GUILD
        with self.assertRaises(sync.SyncError):
            sync.validate_config(config)


if __name__ == "__main__":
    unittest.main()
