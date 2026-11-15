"""Gera o convite do bot próprio; não pede token nem cria uma aplicação."""
import argparse
import re
from urllib.parse import urlencode

# Gerir a estrutura e conceder apenas os poderes declarados aos cargos gerenciados.
# Não inclui Administrador, expulsão, banimento ou gestão de webhooks.
PERMISSION_BITS = (4, 5, 6, 7, 10, 11, 13, 14, 15, 16, 20, 21, 28, 31, 34, 35, 36, 38, 40)
BOT_PERMISSIONS = sum(1 << bit for bit in PERMISSION_BITS)
GUILD_ID = '1571058974625628182'


def invitation(application_id: str) -> str:
    # Snowflakes are unsigned 64-bit integers; their decimal length can vary.
    # Numeric validation does not establish that a Discord application exists.
    if (not isinstance(application_id, str)
            or not re.fullmatch(r'[0-9]{1,20}', application_id)
            or not 1 <= int(application_id) <= 2**64 - 1):
        raise ValueError('Application ID deve ser um inteiro positivo de até 64 bits, com 1 a 20 dígitos.')
    return 'https://discord.com/oauth2/authorize?' + urlencode({
        'client_id': application_id,
        'scope': 'bot',
        'permissions': str(BOT_PERMISSIONS),
        'guild_id': GUILD_ID,
        'disable_guild_select': 'true',
    })


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application_id')
    args = parser.parse_args()
    try:
        print(invitation(args.application_id))
    except ValueError as exc:
        parser.error(str(exc))
