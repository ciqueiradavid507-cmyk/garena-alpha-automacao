"""Conservative, finite Discord REST tasks for GitHub Actions.

Only resources explicitly named by server.json are reconciled. No operation
deletes channels, roles or messages, or assigns member roles.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ADMINISTRATOR = 1 << 3
MANAGE_CHANNELS = 1 << 4
VIEW_AUDIT_LOG = 1 << 7
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
MANAGE_MESSAGES = 1 << 13
EMBED_LINKS = 1 << 14
ATTACH_FILES = 1 << 15
READ_MESSAGE_HISTORY = 1 << 16
CONNECT = 1 << 20
SPEAK = 1 << 21
MANAGE_GUILD = 1 << 5
MANAGE_ROLES = 1 << 28
MANAGE_WEBHOOKS = 1 << 29
CREATE_PUBLIC_THREADS = 1 << 35
CREATE_PRIVATE_THREADS = 1 << 36
SEND_MESSAGES_IN_THREADS = 1 << 38
MANAGE_THREADS = 1 << 34
MODERATE_MEMBERS = 1 << 40
WRITE = (SEND_MESSAGES | CREATE_PUBLIC_THREADS | CREATE_PRIVATE_THREADS
         | SEND_MESSAGES_IN_THREADS)
PARTICIPATE = (VIEW_CHANNEL | SEND_MESSAGES | READ_MESSAGE_HISTORY
               | EMBED_LINKS | ATTACH_FILES | CONNECT | SPEAK | (1 << 6)
               | (1 << 31))
BOT_CHANNEL_ACCESS = (VIEW_CHANNEL | SEND_MESSAGES | READ_MESSAGE_HISTORY
                      | EMBED_LINKS | ATTACH_FILES | SEND_MESSAGES_IN_THREADS)
INTEGRATION_CHANNEL_ACCESS = (BOT_CHANNEL_ACCESS | CREATE_PRIVATE_THREADS
                              | MANAGE_THREADS | MANAGE_MESSAGES)
SENSITIVE = {
    "Administrador": ADMINISTRATOR,
    "Gerenciar servidor": MANAGE_GUILD,
    "Gerenciar cargos": MANAGE_ROLES,
    "Gerenciar webhooks": MANAGE_WEBHOOKS,
}
SNOWFLAKE = re.compile(r"^[0-9]{17,20}$")
KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class SyncError(Exception):
    """A configuration or Discord operation could not be completed safely."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        # Never forward the bot Authorization header to a redirected host.
        return None


def _snowflake(value):
    return (isinstance(value, str) and bool(SNOWFLAKE.fullmatch(value))
            and int(value) < 2**64)


def _integer(value, label):
    if isinstance(value, bool):
        raise SyncError(f"{label}: inteiro inválido.")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.isdigit():
        number = int(value)
    else:
        raise SyncError(f"{label}: inteiro inválido.")
    if number < 0:
        raise SyncError(f"{label}: valor negativo.")
    return number


def validate_config(config):
    """Validate the public configuration without any token or network access."""
    if not isinstance(config, dict) or not _snowflake(config.get("guild_id")):
        raise SyncError("guild_id deve ser um ID válido de servidor Discord.")
    roles = config.get("roles")
    categories = config.get("categories")
    if not isinstance(roles, list) or not isinstance(categories, list):
        raise SyncError("roles e categories devem ser listas.")
    keys = {"role": set(), "category": set(), "channel": set()}
    resource_ids = {"role": set(), "channel": set()}
    role_names, channel_names = set(), set()

    def resource(item, names, kind):
        if not isinstance(item, dict):
            raise SyncError("Recurso inválido na configuração.")
        key, name = item.get("key"), item.get("name")
        if not isinstance(key, str) or not KEY.fullmatch(key) or key in keys[kind]:
            raise SyncError("Chave de recurso inválida ou duplicada.")
        keys[kind].add(key)
        if "id" in item:
            resource_id = item["id"]
            namespace = "role" if kind == "role" else "channel"
            if not _snowflake(resource_id) or resource_id in resource_ids[namespace]:
                raise SyncError(f"ID de recurso inválido ou duplicado: {key}.")
            if kind == "role" and resource_id == config["guild_id"]:
                raise SyncError("O cargo @everyone não pode ser gerenciado por ID.")
            resource_ids[namespace].add(resource_id)
        if (not isinstance(name, str) or not name.strip() or len(name) > 100
                or "\n" in name or "\r" in name):
            raise SyncError(f"Nome inválido no recurso {key}.")
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list) or any(
                not isinstance(alias, str) or not alias.strip()
                or len(alias) > 100 for alias in aliases):
            raise SyncError(f"Aliases inválidos em {key}.")
        own_names = {name, *aliases}
        if names & own_names:
            raise SyncError(f"Nome ou alias compartilhado por recursos: {key}.")
        names.update(own_names)

    for role in roles:
        resource(role, role_names, "role")
        if "@everyone" in {role["name"], *role.get("aliases", [])}:
            raise SyncError("Não configure o cargo @everyone como cargo gerenciado.")
        permissions = _integer(role.get("permissions", "0"), "permissions")
        if permissions & ADMINISTRATOR:
            raise SyncError("Administrador não pode ser concedido pela configuração.")
        if _integer(role.get("color", 0), "color") > 0xFFFFFF:
            raise SyncError("Cor de cargo fora da faixa RGB.")
    role_keys = {role["key"] for role in roles}
    # Role, category and channel keys are separate namespaces.

    def references(values, label):
        if (not isinstance(values, list) or not values
                or any(not isinstance(key, str) for key in values)
                or len(set(values)) != len(values)
                or any(key not in role_keys | {"@everyone"} for key in values)):
            raise SyncError(f"Referências de cargos inválidas: {label}.")

    channel_by_key = {}
    for category in categories:
        resource(category, channel_names, "category")
        access = category.get("access")
        references(access, category["key"])
        if "@everyone" in access and len(access) != 1:
            raise SyncError("Use somente @everyone para categorias públicas.")
        if not isinstance(category.get("channels"), list):
            raise SyncError("channels deve ser uma lista.")
        for channel in category["channels"]:
            resource(channel, channel_names, "channel")
            if type(channel.get("type")) is not int or channel["type"] not in (0, 2):
                raise SyncError(f"Tipo de canal inválido: {channel['key']}.")
            if "write_roles" in channel:
                writers = channel["write_roles"]
                if writers != []:
                    references(writers, channel["key"])
                if "@everyone" in writers:
                    raise SyncError("write_roles deve conter cargos específicos.")
                if "@everyone" not in access and not set(writers) <= set(access):
                    raise SyncError("Autor de canal privado deve ter acesso à categoria.")
            topic = channel.get("topic", "")
            if not isinstance(topic, str) or len(topic) > 1024:
                raise SyncError("Descrição de canal inválida.")
            if "user_limit" in channel:
                if (_integer(channel["user_limit"], "user_limit") > 99
                        or channel["type"] != 2):
                    raise SyncError("Limite de participantes inválido.")
            channel_by_key[channel["key"]] = channel
    integrations = config.get("integrations", [])
    if not isinstance(integrations, list):
        raise SyncError("integrations deve ser uma lista.")
    integration_ids = set()
    for integration in integrations:
        if not isinstance(integration, dict):
            raise SyncError("Integração inválida.")
        bot_id = integration.get("bot_id")
        targets = integration.get("channels")
        if (not _snowflake(bot_id) or bot_id in integration_ids
                or not isinstance(integration.get("name"), str)
                or not 1 <= len(integration["name"]) <= 100
                or not isinstance(targets, list) or not targets
                or any(not isinstance(key, str) or key not in channel_by_key for key in targets)
                or len(set(targets)) != len(targets)):
            raise SyncError("Integração deve ter bot_id único e canais configurados válidos.")
        integration_ids.add(bot_id)
    announcements = config.get("announcements", {})
    if not isinstance(announcements, dict):
        raise SyncError("announcements deve ser um objeto.")
    for kind, announcement in announcements.items():
        if kind not in ("coordination", "agenda") or not isinstance(announcement, dict):
            raise SyncError("Tipo de anúncio não permitido.")
        target = channel_by_key.get(announcement.get("channel"))
        content = announcement.get("content")
        if (target is None or target["type"] != 0 or not isinstance(content, str)
                or not 1 <= len(content) <= 2000):
            raise SyncError("Anúncio deve apontar a canal de texto configurado e conter 1–2000 caracteres.")


class DiscordClient:
    """Small REST client. Error output never includes response bodies or tokens."""

    def __init__(self, token, *, sleeper=time.sleep, opener=None):
        if not isinstance(token, str) or not token.strip():
            raise SyncError("Configure o secret DISCORD_BOT_TOKEN no GitHub Actions.")
        self.token = token.strip()
        self.sleeper = sleeper
        self.opener = opener or urllib.request.build_opener(_NoRedirect()).open

    def request(self, method, path, payload=None):
        if (not isinstance(path, str) or not path.startswith("/") or ".." in path
                or not re.fullmatch(r"/[A-Za-z0-9_/@.-]+", path)):
            raise SyncError("Caminho REST não permitido.")
        method = method.upper()
        if method not in {"GET", "POST", "PATCH", "PUT"}:
            raise SyncError("Método REST não permitido.")
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        for attempt in range(4):
            request = urllib.request.Request(
                "https://discord.com/api/v10" + path, data=data, method=method,
                headers={"Authorization": "Bot " + self.token,
                         "Content-Type": "application/json",
                         "User-Agent": "DiscordBot (https://github.com, 1.0)"})
            try:
                with self.opener(request, timeout=30) as response:
                    body = response.read()
                    return json.loads(body) if body else None
            except urllib.error.HTTPError as error:
                details = {}
                try:
                    parsed = json.loads(error.read(8192))
                    if isinstance(parsed, dict):
                        details = parsed
                except (ValueError, UnicodeError, OSError):
                    pass
                error_context = f"Discord respondeu HTTP {error.code} em {method} {path}"
                discord_code = details.get("code")
                if type(discord_code) is int:
                    error_context += f" (código Discord {discord_code})"
                if error.code == 429 and attempt < 3:
                    delay = 1.0
                    try:
                        delay = float(details.get("retry_after", 1))
                    except (ValueError, TypeError, AttributeError):
                        pass
                    if not 0 <= delay <= 30:
                        raise SyncError(error_context + "; tente novamente mais tarde.") from None
                    self.sleeper(max(0.1, delay))
                    continue
                # POST can have succeeded before a gateway error: never retry it
                # on 5xx or network errors, which could create duplicates.
                if error.code >= 500 and method != "POST" and attempt < 3:
                    self.sleeper(2**attempt)
                    continue
                raise SyncError(error_context + "; nenhuma credencial foi exibida.") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise SyncError("Falha de rede ao acessar Discord; confira o estado antes de repetir uma criação ou anúncio.") from None
            except (ValueError, UnicodeError):
                raise SyncError("Discord retornou uma resposta inválida.") from None
        raise SyncError("Limite de tentativas REST atingido.")


def _find(item, inventory, *, category=False, channel_type=None):
    if "id" in item:
        if not _snowflake(item["id"]):
            raise SyncError(f"ID de recurso inválido: {item['key']}.")
        matches = [resource for resource in inventory
                   if str(resource.get("id")) == item["id"]]
        if not matches:
            raise SyncError(f"Recurso {item['key']} com ID {item['id']} não encontrado; criação de substituto bloqueada.")
    else:
        names = {item["name"], *item.get("aliases", [])}
        matches = [resource for resource in inventory if resource.get("name") in names]
    if len(matches) > 1:
        raise SyncError(f"Correspondência ambígua para {item['key']}; informe nomes/aliases únicos.")
    if not matches:
        return None
    match = matches[0]
    if category and match.get("type") != 4:
        raise SyncError(f"Categoria {item['key']} corresponde a recurso de outro tipo.")
    if channel_type is not None:
        compatible = {0, 5} if channel_type == 0 else {2}
        if match.get("type") not in compatible:
            raise SyncError(f"Canal {item['key']} corresponde a recurso de outro tipo.")
    return match


def _normalized_overwrites(values):
    return sorted(
        [{"id": str(item["id"]), "type": int(item["type"]),
          "allow": str(_integer(item.get("allow", "0"), "allow")),
          "deny": str(_integer(item.get("deny", "0"), "deny"))} for item in values],
        key=lambda item: (item["type"], item["id"]))


def _overwrites(access, role_ids, guild_id, bot_id, writers=None, integration_roles=()):
    public = "@everyone" in access
    rows = {guild_id: {"id": guild_id, "type": 0,
                       "allow": PARTICIPATE if public else 0,
                       "deny": 0 if public else VIEW_CHANNEL}}
    for key in access:
        if key != "@everyone":
            rows[role_ids[key]] = {"id": role_ids[key], "type": 0,
                                   "allow": PARTICIPATE, "deny": 0}
    if writers is not None:
        for row in rows.values():
            row["allow"] &= ~WRITE
            row["deny"] |= WRITE
        for key in writers:
            role_id = role_ids[key]
            row = rows.setdefault(role_id, {"id": role_id, "type": 0,
                                            "allow": PARTICIPATE, "deny": 0})
            row["allow"] |= WRITE
            row["deny"] &= ~WRITE
    output = list(rows.values())
    # A member exception keeps the automation bot able to announce in read-only
    # channels and inspect private channels, without exposing them to all bots.
    output.append({"id": bot_id, "type": 1, "allow": BOT_CHANNEL_ACCESS, "deny": 0})
    for role_id in integration_roles:
        output.append({"id": role_id, "type": 0,
                       "allow": INTEGRATION_CHANNEL_ACCESS, "deny": 0})
    return _normalized_overwrites(output)


def _changed(payload, current):
    for key, value in payload.items():
        actual = current.get(key)
        if key == "permission_overwrites":
            if _normalized_overwrites(actual or []) != value:
                return True
        elif key == "permissions":
            if _integer(actual or "0", key) != int(value):
                return True
        elif key == "topic":
            if (actual or "") != value:
                return True
        elif actual != value:
            return True
    return False


def build_plan(config, roles, channels, bot_user, bot_member):
    """Pure reconciliation plan. Inventories are Discord REST JSON values."""
    validate_config(config)
    guild_id, bot_id = config["guild_id"], str(bot_user.get("id", ""))
    if not _snowflake(bot_id):
        raise SyncError("Identidade do bot inválida.")
    if not isinstance(roles, list) or not isinstance(channels, list):
        raise SyncError("Inventário retornado pelo Discord inválido.")
    roles_by_id = {str(role["id"]): role for role in roles}
    if guild_id not in roles_by_id:
        raise SyncError("Cargo @everyone não encontrado no servidor alvo.")
    bot_role_ids = {str(value) for value in bot_member.get("roles", [])}
    bot_roles = [roles_by_id[key] for key in bot_role_ids if key in roles_by_id]
    bot_permissions = _integer(roles_by_id[guild_id].get("permissions", "0"), "permissions")
    for role in bot_roles:
        bot_permissions |= _integer(role.get("permissions", "0"), "permissions")
    bot_top = max([int(role.get("position", 0)) for role in bot_roles], default=0)
    administrator = bool(bot_permissions & ADMINISTRATOR)
    operations, warnings, pending_manual = [], [], []
    role_ids, category_ids, channel_ids = {}, {}, {}
    matched_roles = []
    integration_channels, integration_status = {}, []
    for integration in config.get("integrations", []):
        matches = [role for role in roles if role.get("managed")
                   and str((role.get("tags") or {}).get("bot_id", "")) == integration["bot_id"]]
        if len(matches) > 1:
            raise SyncError("Mais de um cargo gerenciado corresponde à integração configurada.")
        present = bool(matches)
        integration_status.append({"name": integration["name"], "present": present,
                                   "channel_keys": integration["channels"]})
        if present:
            for channel_key in integration["channels"]:
                integration_channels.setdefault(channel_key, []).append(str(matches[0]["id"]))
        else:
            pending_manual.append("Integração " + json.dumps(integration["name"], ensure_ascii=False)
                                  + " não encontrada por tags.bot_id; exceções de acesso não foram criadas. Adicione o bot e execute novamente.")

    for role in config["roles"]:
        current = _find(role, roles)
        if current and current.get("managed"):
            raise SyncError(f"{role['key']} corresponde a cargo de integração; ele será preservado. Altere os aliases.")
        role_id = str(current["id"]) if current else "$role:" + role["key"]
        role_ids[role["key"]] = role_id
        payload = {"name": role["name"], "permissions": str(role.get("permissions", "0")),
                   "color": int(role.get("color", 0))}
        if current:
            matched_roles.append((role["key"], int(current.get("position", 0))))
        if current is None or _changed(payload, current):
            operation = {"kind": "role.update" if current else "role.create",
                         "key": role["key"], "id": str(current["id"]) if current else None,
                         "payload": payload}
            if not administrator and not bot_permissions & MANAGE_ROLES:
                operation["blocked_reason"] = "O bot precisa de Gerenciar cargos."
            elif current and int(current.get("position", 0)) >= bot_top:
                operation["blocked_reason"] = "Cargo no mesmo nível ou acima do bot; ajuste manual necessário."
            elif not administrator and int(payload["permissions"]) & ~bot_permissions:
                operation["blocked_reason"] = "O cargo solicita permissões que o próprio bot não possui."
            operations.append(operation)

    expected = [key for key, _ in matched_roles]
    actual = [key for key, _ in sorted(matched_roles, key=lambda item: item[1], reverse=True)]
    equal_positions = len({position for _, position in matched_roles}) != len(matched_roles)
    if expected != actual or equal_positions:
        pending_manual.append("A ordem dos cargos existentes não segue a ordem de roles na configuração; ajuste a hierarquia manualmente. A automação não reordena terceiros.")
    if any(value.startswith("$role:") for value in role_ids.values()):
        warnings.append("Novos cargos são criados abaixo do bot. A hierarquia será conferida novamente após a aplicação.")

    managed_ids = set(role_ids.values())
    for role in roles:
        permission_bits = _integer(role.get("permissions", "0"), "permissions")
        if str(role["id"]) not in managed_ids:
            flags = [label for label, bit in SENSITIVE.items() if permission_bits & bit]
            if flags:
                warnings.append("Cargo fora do escopo gerenciado: " + json.dumps(role.get("name", ""), ensure_ascii=False)
                                + "; permissões sensíveis: " + ", ".join(flags) + ". Revisão manual; sem alteração automática.")
                if str(role["id"]) == guild_id:
                    pending_manual.append("O cargo @everyone possui permissões administrativas sensíveis. Revise-as manualmente antes de considerar o servidor configurado.")

    managed_channel_ids = set()
    for category in config["categories"]:
        for channel in category["channels"]:
            existing = _find(channel, channels, channel_type=channel["type"])
            if existing:
                managed_channel_ids.add(str(existing["id"]))

    def add_channel_operation(item, current, payload, *, category=False):
        if current is not None and not _changed(payload, current):
            return
        operation = {"kind": ("category" if category else "channel") + (".update" if current else ".create"),
                     "key": item["key"], "id": str(current["id"]) if current else None,
                     "payload": payload}
        if not administrator and not bot_permissions & MANAGE_CHANNELS:
            operation["blocked_reason"] = "O bot precisa de Gerenciar canais."
        elif not administrator and not bot_permissions & MANAGE_ROLES:
            operation["blocked_reason"] = "O bot precisa de Gerenciar cargos para configurar permissões de canais."
        elif not administrator:
            requested = 0
            for overwrite in payload.get("permission_overwrites", []):
                requested |= int(overwrite["allow"])
            if requested & ~bot_permissions:
                operation["blocked_reason"] = "O canal solicita permissões que o próprio bot não possui."
        if category and current:
            old_acl = _normalized_overwrites(current.get("permission_overwrites", []))
            acl_changes = old_acl != payload.get("permission_overwrites", [])
            inherited_unmanaged = [child for child in channels
                                   if str(child.get("parent_id")) == str(current["id"])
                                   and str(child["id"]) not in managed_channel_ids
                                   and _normalized_overwrites(child.get("permission_overwrites", [])) == old_acl]
            if acl_changes and inherited_unmanaged:
                operation["blocked_reason"] = ("A categoria contém canais fora do escopo com permissões sincronizadas; "
                                               "a mudança poderia alterar esses canais. Inclua-os no modelo ou ajuste-os manualmente primeiro.")
        operations.append(operation)

    for category in config["categories"]:
        current = _find(category, channels, category=True)
        category_id = str(current["id"]) if current else "$category:" + category["key"]
        category_ids[category["key"]] = category_id
        payload = {"name": category["name"],
                   "permission_overwrites": _overwrites(category["access"], role_ids, guild_id, bot_id)}
        if not current:
            payload["type"] = 4
        add_channel_operation(category, current, payload, category=True)
        for channel in category["channels"]:
            current_channel = _find(channel, channels, channel_type=channel["type"])
            channel_ids[channel["key"]] = (str(current_channel["id"]) if current_channel
                                            else "$channel:" + channel["key"])
            payload = {"name": channel["name"], "parent_id": category_id,
                       "permission_overwrites": _overwrites(category["access"], role_ids, guild_id, bot_id,
                                                              channel.get("write_roles"),
                                                              integration_channels.get(channel["key"], []))}
            if channel["type"] == 0:
                payload["topic"] = channel.get("topic", "")
            elif "user_limit" in channel:
                payload["user_limit"] = int(channel["user_limit"])
            if current_channel is None:
                payload["type"] = channel["type"]
            add_channel_operation(channel, current_channel, payload)

    if any(operation["kind"].startswith(("category.", "channel.")) for operation in operations):
        warnings.append("As permissões por cargo e por pessoa dos canais/categorias gerenciados serão substituídas pelo modelo, mantendo exceções para este bot e integrações explicitamente permitidas. Outras exceções existentes serão removidas.")
    warnings.append("Proprietário e pessoas com Administrador continuam podendo acessar canais privados. A automação não remove esses poderes de cargos fora do escopo.")
    return {"guild_id": guild_id, "bot_user_id": bot_id,
            "operations": operations, "warnings": warnings,
            "pending_manual": pending_manual,
            "integrations": integration_status,
            "resources": {"roles": role_ids, "categories": category_ids, "channels": channel_ids},
            "summary": {"changes": len(operations),
                        "blocked": sum("blocked_reason" in operation for operation in operations)}}


def _inventory(config, client):
    guild_id = config["guild_id"]
    bot_user = client.request("GET", "/users/@me")
    if not isinstance(bot_user, dict) or not _snowflake(str(bot_user.get("id", ""))):
        raise SyncError("Não foi possível verificar a identidade do bot.")
    roles = client.request("GET", f"/guilds/{guild_id}/roles")
    channels = client.request("GET", f"/guilds/{guild_id}/channels")
    bot_member = client.request("GET", f"/guilds/{guild_id}/members/{bot_user['id']}")
    if not isinstance(bot_member, dict):
        raise SyncError("Não foi possível verificar o acesso do bot ao servidor.")
    return roles, channels, bot_user, bot_member


def _resolve(value, ids):
    if isinstance(value, str) and value.startswith(("$role:", "$category:", "$channel:")):
        if value not in ids:
            raise SyncError("Dependência de recurso não resolvida; a aplicação foi interrompida.")
        return ids[value]
    if isinstance(value, list):
        return [_resolve(item, ids) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, ids) for key, item in value.items()}
    return value


def reconcile(config, client, apply=False):
    """Return a read-only plan, or apply it and read the state back."""
    validate_config(config)
    plan = build_plan(config, *_inventory(config, client))
    plan["mode"] = "plan"
    if not apply:
        return plan
    if plan["summary"]["blocked"]:
        raise SyncError("Existem alterações bloqueadas por permissões/hierarquia. Execute plan e resolva as pendências; nenhuma alteração foi iniciada.")
    guild_id, ids, completed = config["guild_id"], {}, []
    for operation in plan["operations"]:
        kind = operation["kind"]
        is_role = kind.startswith("role.")
        payload = _resolve(copy.deepcopy(operation["payload"]), ids)
        if kind.endswith(".create"):
            path = f"/guilds/{guild_id}/" + ("roles" if is_role else "channels")
            response = client.request("POST", path, payload)
            if not isinstance(response, dict) or not _snowflake(str(response.get("id", ""))):
                raise SyncError("Discord não confirmou o ID do recurso criado. Verifique o servidor antes de repetir.")
            resource_id = str(response["id"])
            placeholder = "$" + kind.split(".")[0] + ":" + operation["key"]
            ids[placeholder] = resource_id
        else:
            resource_id = operation["id"]
            path = (f"/guilds/{guild_id}/roles/{resource_id}" if is_role
                    else f"/channels/{resource_id}")
            client.request("PATCH", path, payload)
        completed.append({"kind": kind, "key": operation["key"], "id": resource_id})
    remaining = build_plan(config, *_inventory(config, client))
    return {"mode": "apply", "guild_id": guild_id, "completed": completed,
            "remaining_operations": remaining["operations"],
            "verified_configuration": not remaining["operations"] and not remaining["pending_manual"],
            "warnings": remaining["warnings"], "pending_manual": remaining["pending_manual"],
            "integrations": remaining["integrations"],
            "summary": {"completed": len(completed), "remaining": len(remaining["operations"])}}


def announce(config, client, kind):
    """Publish one configured message, without user/role/everyone mentions."""
    validate_config(config)
    if kind not in ("coordination", "agenda") or kind not in config.get("announcements", {}):
        raise SyncError("Selecione um anúncio configurado: coordination ou agenda.")
    announcement = config["announcements"][kind]
    target = None
    for category in config["categories"]:
        for channel in category["channels"]:
            if channel["key"] == announcement["channel"]:
                target = channel
    roles, inventory, bot_user, bot_member = _inventory(config, client)
    plan = build_plan(config, roles, inventory, bot_user, bot_member)
    if plan["operations"] or plan["pending_manual"]:
        raise SyncError("Há divergências ou pendências de configuração. Execute plan/apply e confira o resultado antes de enviar anúncios.")
    channel = _find(target, inventory, channel_type=0)
    if channel is None:
        raise SyncError("Canal do anúncio ainda não existe; aplique a configuração primeiro.")
    response = client.request("POST", f"/channels/{channel['id']}/messages", {
        "content": announcement["content"],
        "allowed_mentions": {"parse": [], "replied_user": False}})
    if not isinstance(response, dict) or not _snowflake(str(response.get("id", ""))):
        raise SyncError("Não foi possível confirmar o envio. Confira o canal antes de repetir.")
    return {"mode": "announce", "kind": kind, "channel_id": str(channel["id"]),
            "message_id": str(response["id"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Configuração finita do Garena Alpha via Discord REST")
    parser.add_argument("mode", choices=("validate", "plan", "apply", "announce"))
    parser.add_argument("--config", default="config/server.json")
    parser.add_argument("--kind", choices=("coordination", "agenda"))
    args = parser.parse_args(argv)
    try:
        with Path(args.config).open(encoding="utf-8") as handle:
            config = json.load(handle)
        validate_config(config)
        if args.mode == "validate":
            result = {"mode": "validate", "valid": True, "guild_id": config["guild_id"]}
        else:
            client = DiscordClient(os.environ.get("DISCORD_BOT_TOKEN", ""))
            if args.mode == "announce":
                result = announce(config, client, args.kind)
            else:
                result = reconcile(config, client, apply=args.mode == "apply")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.mode == "apply" and not result["verified_configuration"]:
            return 2
        return 0
    except (SyncError, json.JSONDecodeError, OSError) as error:
        # File errors contain paths but no credentials. Never print REST bodies.
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
