# Garena Alpha — configuração e automações

Este repositório mantém a estrutura do servidor **GA | Garena Alpha ⚠️** (`1571058974625628182`) e executa rotinas curtas pela API do Discord. Não usa IA nem créditos por comando.

O código funciona sem serviço de hospedagem contínua. Em repositório público, os runners padrão do GitHub Actions são gratuitos, sujeitos aos limites e termos do GitHub. Se o repositório se tornar privado, as execuções usam a franquia da conta e podem gerar cobrança se ela for excedida. As rotinas programadas começam desativadas.

## O que cada componente faz

| Componente | Responsabilidade |
|---|---|
| `config/server.json` | Cargos, canais, áreas privadas e textos dos lembretes |
| Configurar Discord | Mostrar um plano ou aplicar a configuração |
| Rotinas da comunidade | Auditoria de estrutura e lembretes de coordenação/agenda |
| Rezaletbot, já adicionado pelo proprietário | Boas-vindas, atendimento por tickets, registros e AutoMod em tempo real |

Um webhook simples do GitHub só publica mensagens. Ele não administra canais e cargos. O GitHub também não disponibiliza o token de bots de terceiros.

## Conectar o bot próprio

1. Acesse [Discord Developer Portal](https://discord.com/developers/applications) e crie uma aplicação chamada **Garena Alpha — Organização**.
2. Na página **Bot**, gere o token. Salve-o diretamente em **Settings → Secrets and variables → Actions → New repository secret**, neste repositório, com o nome **`DISCORD_BOT_TOKEN`**. Não publique o token em código, issues, mensagens ou capturas de tela.
3. Copie o **Application ID**, que não é secreto. Gere o convite com `python3 -m automation.invite_url APPLICATION_ID`. O link já seleciona o Garena Alpha e não solicita Administrador.
4. Autorize a instalação com a conta responsável pelo servidor. O bot usa a API REST; não precisa ativar Message Content ou Server Members Intent e pode aparecer offline.
5. Posicione o cargo do bot acima dos cargos que ele precisará configurar. Cargos iguais ou superiores ao bot e cargos controlados por integrações não podem ser modificados por ele. Resolva essas pendências manualmente; não remova o proprietário nem poderes necessários de integrações existentes.

As permissões do convite incluem gestão de canais/cargos/servidor e os poderes que o bot precisa poder conceder à Moderação. O programa não executa banimentos, expulsões ou timeouts, não atribui cargos a pessoas e não usa Administrador.

## Aplicar a configuração

1. Abra **Actions → Configurar Discord → Run workflow** e mantenha `modo: plan`. Se houver nomes ambíguos, use `modo: inspect` para listar IDs, tipos e categorias dos candidatos declarados, sem mensagens, conteúdo ou permissões individuais de pessoas.
2. Leia as mudanças e pendências. O plano faz consultas e não altera o servidor. Se houver correspondências ambíguas, ajuste os nomes/aliases em `config/server.json` para identificar o recurso correto.
3. Execute novamente com `modo: apply`. Não é necessário publicar código ou compartilhar o token para fazer isso.
4. Execute `plan` de novo. Não deve haver mudanças pendentes nos recursos que o bot conseguiu gerenciar. Confira também as áreas privadas com uma conta de participante, sem poderes administrativos.

O programa reaproveita o `id` explícito quando informado; caso contrário, usa nomes e aliases inequívocos. IDs inexistentes interrompem a execução sem criar substitutos. Não exclui canais, cargos, mensagens ou membros. Ele pode renomear, mover e atualizar os recursos declarados. **Nos canais e categorias gerenciados, substitui as permissões explícitas pela configuração**, removendo exceções antigas de cargos e pessoas. Antes de aplicar, inclua no arquivo todos os acessos que devem permanecer. Isso não apaga mensagens, mas pode retirar o acesso de integrações não declaradas. A configuração inclui uma exceção explícita para o Rezaletbot, identificada por seu cargo gerenciado e ID do bot.

A inspeção real identificou canais duplicados. O modelo fixa por ID os recursos escolhidos e preserva o ID do canal oficial de regras (`regras-da-comunidade`). O canal de atualizações administrativas do Discord fica fora do escopo, e o canal original de mensagens do sistema é reaproveitado como `boas-vindas`. Canais e cargos legados não declarados permanecem intactos.

Se um canal não consta no arquivo, o programa não altera suas permissões. Ele também não altera as permissões globais de `@everyone`, não remove poderes de cargos externos à configuração e não prova a privacidade de todo o servidor só por terminar uma execução. Pessoas com Administrador ou o proprietário podem acessar áreas privadas. Avisos de auditoria precisam ser revisados.

## Organização prevista

- **Administração/Moderação:** funções técnicas separadas das funções de jogo.
- **Líder, Vice-líder e Capitão:** organização da guilda; sem gestão técnica do Discord.
- **Membro da Guilda:** área privada da guilda, depois de aprovação manual.
- **Recruta e Comunidade:** áreas públicas conforme a verificação já existente no Discord.
- **Patentes BR/CS:** não são sincronizadas com o jogo e não concedem poderes adicionais.
- **Informações:** leitura pública; escrita restrita aos responsáveis.
- **Equipe:** Administração e Moderação.

## Recepção e atendimento pelo Rezaletbot

No painel do Rezaletbot, selecione o Garena Alpha:

- Boas-vindas em `boas-vindas`, indicando `regras-da-comunidade`; sem DM e sem cargo automático na entrada. Preserve a verificação Alta do Discord.
- Direcione os registros a um canal privado sob responsabilidade da equipe.
- Ative antispam e limite inicial de cinco menções; use bloqueio e alertas, sem punições automáticas.
- Configure tickets de suporte, recrutamento e denúncia com acesso somente ao solicitante e aos responsáveis apropriados.

O arquivo permite acesso do Rezaletbot a `geral`, `boas-vindas`, `regras-da-comunidade`, `atendimento` e `registros-moderação`. A automação resolve o cargo somente quando ele é gerenciado e `tags.bot_id` corresponde ao ID configurado; se não o encontrar, relata a pendência. O bot não recebe acesso aos demais chats privados. Para outros bots, declare explicitamente uma integração equivalente antes de aplicar. Preserve os tickets em categoria própria, fora das categorias gerenciadas, pois os acessos individuais de cada ticket não são mantidos por este modelo. O canal `atendimento` é preparado para receber um painel, mas o GitHub não instala o painel do Rezaletbot.

Estas opções exigem configuração no painel do Rezaletbot. O GitHub não foi autenticado nesse painel e não o configura automaticamente.

## Rotinas opcionais

O workflow **Rotinas da comunidade** permite executar manualmente:

- `auditoria`: somente leitura; mostra diferenças entre o arquivo e o Discord.
- `coordenacao`: publica o texto configurado em `coordenacao`.
- `agenda`: publica o texto configurado em `agenda`.

Para ativar horários, crie variáveis em **Settings → Secrets and variables → Actions → Variables**:

| Variável | Efeito quando igual a `true` |
|---|---|
| `GARENA_AUTOMATION_ENABLED` | Auditoria diária às 06h35 de Belém |
| `GARENA_ANNOUNCEMENTS_ENABLED` | Além da anterior, coordenação segunda às 18h30 e agenda sexta às 18h30 |

Os crons usam UTC; os horários acima usam **America/Belem (UTC−3)**. O GitHub pode atrasar execuções agendadas. As publicações não marcam pessoas/cargos e não criam eventos nem confirmam premiações. Reexecutar manualmente uma publicação pode enviar uma nova mensagem.

## Verificação local

Python 3.10 ou superior, sem instalar bibliotecas externas:

```sh
python3 -m automation.discord_sync validate
python3 -m unittest discover -s tests -v
```

Os testes usam dados simulados. Para testar o servidor real, primeiro conecte o bot e execute `plan`. A configuração aplicada deve ser lida novamente para confirmar o resultado.

## Referências

- [Permissões do Discord](https://docs.discord.com/developers/topics/permissions)
- [API de guildas](https://docs.discord.com/developers/resources/guild)
- [GitHub Actions Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
- [Uso e cobrança do GitHub Actions](https://docs.github.com/en/actions/concepts/billing-and-usage)
- [Assistente do Rezaletbot](https://rezaletbot.com/pt/features/server-setup)
