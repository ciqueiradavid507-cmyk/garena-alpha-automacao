"""Inspeção somente de leitura dos candidatos declarados no modelo.

Não consulta mensagens, lista de membros, token, conteúdo ou permissões de pessoas.
Nomes de canais/cargos fora dos aliases do arquivo não são publicados no relatório.
"""
import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path

from .discord_sync import DiscordClient, SyncError, validate_config


def inspect(config, client):
    validate_config(config)
    guild_id = config['guild_id']
    bot = client.request('GET', '/users/@me')
    roles = client.request('GET', f'/guilds/{guild_id}/roles')
    channels = client.request('GET', f'/guilds/{guild_id}/channels')
    member = client.request('GET', f'/guilds/{guild_id}/members/{bot["id"]}')
    guild = client.request('GET', f'/guilds/{guild_id}')
    if not isinstance(roles, list) or not isinstance(channels, list):
        raise SyncError('Inventário do Discord inválido.')

    def candidates(spec, items, fields):
        names = {spec['name'], *spec.get('aliases', [])}
        def normalized(name):
            return ''.join(c.lower() for c in unicodedata.normalize('NFKD', name)
                           if c.isalnum())
        forms = {normalized(name) for name in names} - {''}
        return [{field: item.get(field) for field in fields}
                for item in items if item.get('name') in names
                or normalized(item.get('name', '')) in forms]

    channel_specs = [spec for category in config['categories']
                     for spec in [category, *category['channels']]]
    return {
        'mode': 'inspect', 'guild_id': guild_id,
        'guild_settings': {key: guild.get(key) for key in (
            'rules_channel_id', 'public_updates_channel_id', 'system_channel_id', 'verification_level')},
        'bot_user_id': bot['id'], 'bot_role_ids': member.get('roles', []),
        'bot_roles': [{field: role.get(field) for field in ('id', 'position', 'permissions')}
                      for role in roles if role.get('id') in member.get('roles', [])],
        'everyone_permissions': next((r.get('permissions') for r in roles
                                      if r.get('id') == guild_id), None),
        'roles': {spec['key']: candidates(spec, roles,
                                         ('id', 'name', 'position', 'permissions', 'managed'))
                  for spec in config['roles']},
        'channels': {spec['key']: candidates(spec, channels,
                                            ('id', 'name', 'type', 'parent_id'))
                     for spec in channel_specs},
        'integration_presence': [
            {'name': integration['name'], 'present': any(
                role.get('managed') and role.get('tags', {}).get('bot_id') == integration['bot_id']
                for role in roles)} for integration in config.get('integrations', [])],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config/server.json')
    args = parser.parse_args()
    try:
        config = json.loads(Path(args.config).read_text(encoding='utf-8'))
        result = inspect(config, DiscordClient(os.environ.get('DISCORD_BOT_TOKEN', '')))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (SyncError, OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, SyncError):
            message = str(exc)
        else:
            message = 'Não foi possível inspecionar a configuração ou o inventário.'
        print(json.dumps({'error': message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
