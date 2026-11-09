<!-- i18n: source=docs/guide.md sha256=9198a3cbcbff -->
# Guia do Friday

[English](../guide.md) · [Русский](../ru/guide.md) · [简体中文](../zh-cn/guide.md) · [日本語](../ja/guide.md) · [한국어](../ko/guide.md) · **Português (Brasil)** · [Español](../es/guide.md) · [Deutsch](../de/guide.md) · [Français](../fr/guide.md) · [Bahasa Indonesia](../id/guide.md)

_Esta é uma tradução da documentação em inglês; em caso de divergência, vale a versão em inglês._

## Instalação

1. Clone o Friday e adicione `bin` ao `PATH`, ou defina `FRIDAY_BIN`.
2. Instale as CLIs de que os agentes precisam: Claude Code (`claude`) para os modelos da Anthropic e da Z.ai, Codex CLI (`codex`) para os modelos da OpenAI. Faça login nelas como de costume.
3. Para o gate e a aceitação no navegador, instale o Docker. A aceitação no navegador também precisa do Chrome com a porta de depuração 9333 e de uma sessão autenticada no ambiente de teste.
4. O estado fica em `~/.local/share/friday` (altere com `FRIDAY_HOME`). Perfis e segredos não entram no repositório: a pasta `profiles/` está no `.gitignore`.

### Plugin para Codex e Claude Code

```sh
codex plugin marketplace add "$PWD" && codex plugin add friday@friday
claude plugin marketplace add "$PWD" && claude plugin install friday@friday --scope user
```

O plugin ensina o agente a ler `status` e `plan`. Iniciar uma tarefa, aprová-la e tomar decisões ficam por sua conta.

## Como uma tarefa é executada

1. **`start`** cria a branch `friday/KEY` a partir de um `origin/<base_branch>` atualizado e um worktree, fixa a atribuição de papéis e escreve o plano: marcos de 2 a 5 etapas, arquivos, uma estimativa de tamanho, critérios de código e de navegador, e um resumo das regras do projeto para o executor. O plano é verificado primeiro sem modelo (tamanho, arquivos, ordem, critérios). Em seguida, um modelo de outro fornecedor o revisa, e o planejador responde aos achados uma única vez: aceita cada um ou o rejeita com uma justificativa.
2. **`plan`** mostra o plano, as perguntas, os achados rejeitados e as sobreposições de arquivos com outras tarefas ativas.
3. **`approve`** aprova o plano e inicia o pipeline em segundo plano (`--no-run` apenas aprova; execute depois com `run`).
4. **Marco.** O executor trabalha em uma sessão por marco e faz commit de cada etapa. O gate roda sobre o commit. Se falhar, o executor recebe apenas as seções do log que falharam. A revisão do marco analisa o diff desse marco junto com o resultado do gate. O executor corrige ou contesta os achados bloqueantes; os achados contestados são enviados a você. Marcos com interface passam pela aceitação no navegador em um preview isolado. Após duas falhas seguidas, um modelo de nível superior assume o marco em uma nova sessão.
5. **Final.** A revisão final e a verificação cega do diff inteiro rodam em paralelo. Os achados bloqueantes têm uma rodada de correções.
6. **O MR em rascunho** é aberto se o perfil define `delivery`. Caso contrário, o texto dele é salvo em `merge-request.md` na pasta da tarefa.

O pipeline só para quando o gate nunca passou, o executor não fez nada, o ambiente está indisponível ou os modelos permitidos pelas regras se esgotaram. Divergências na revisão não o interrompem.

`decide KEY name "text" --reopen M2` registra a sua decisão. Apenas os marcos indicados e os que dependem deles são reabertos. Sem `--reopen`, os marcos seguintes levam a decisão em conta. `resume` continua uma tarefa interrompida ou com falha. `stop` interrompe uma tarefa em execução: os agentes terminam junto com seus processos filhos.

Para devolver um plano antes de aprová-lo, registre o que mudar com `decide` e execute `replan KEY`. O planejador revisa o plano em espera em uma nova sessão, tendo como motivo as decisões que você registrou depois desse plano. O plano revisado passa de novo pelas verificações e pela revisão de outro fornecedor e então aguarda sua aprovação, mesmo com `approval: auto`. A tarefa mantém seu cartão, histórico e custos. O próprio `decide` nunca executa um modelo, então você pode registrar várias decisões e replanejar uma vez. Se o replanejamento falhar, o plano anterior continua aguardando aprovação. `--brief` fornece de novo o enunciado da tarefa para uma tarefa iniciada antes de o Friday guardá-lo.

## Perfil

Um perfil é um arquivo JSON. A chave `extends` inclui outro perfil (por exemplo, um catálogo de modelos compartilhado); os caminhos são relativos ao arquivo do perfil.

| Chave | Finalidade |
| --- | --- |
| `name`, `repository` | O nome do projeto e o caminho do repositório dele |
| `base_branch`, `worktree_root`, `branch_template`, `worktree_layout` | De onde criar a branch da tarefa e onde colocá-la; o template padrão é `friday/{key}`, o layout é `key` ou `branch` |
| `models`, `roles` | O catálogo de modelos e as preferências de papéis (veja abaixo) |
| `engineering_policy` | Regras para o planejador e as revisões |
| `rules_docs` | Documentos com as regras do projeto. O Friday gera um resumo único deles para o planejador e o revisor do plano (padrão `AGENTS.md`, `CLAUDE.md`, `docs/*.md`). O resumo é guardado pelo hash do conteúdo e reaproveitado pelas tarefas enquanto os documentos não mudam; o planejador não lê os documentos por inteiro |
| `repo_map`, `repo_map_budget`, `repo_map_mcp` | Um mapa do repositório no prompt do planejador e do revisor do plano, para que não explorem a estrutura com chamadas de ferramentas. `none` (padrão): sem mapa. `builtin`: os arquivos versionados com o número de linhas. `graphify`: também os símbolos de cada arquivo e quantos arquivos o importam, tirados de um grafo do [graphify](https://github.com/Graphify-Labs/graphify) (`uv tool install 'graphifyy[mcp]==0.9.100'`; ele apenas analisa o código, nenhum modelo é usado; o grafo fica em `FRIDAY_HOME/maps`, não no repositório). Em uma medição no CS-2, o mapa não tornou o planejamento mais barato (sem mapa US$ 6,19, `builtin` US$ 7,08, `graphify` US$ 7,97, uma execução cada), por isso vem desativado por padrão. O mapa é mantido em cache por commit. Se for maior que `repo_map_budget` caracteres (60000), ele é primeiro reduzido removendo os símbolos e depois recolhendo as pastas. `repo_map_mcp: true` (somente com `graphify`) dá aos dois papéis consultas ao grafo: `query_graph`, `get_node`, `get_neighbors`, `shortest_path`; não verificado ao vivo no Codex. O revisor do plano recebe o texto atual dos arquivos citados no plano (até 40000 caracteres) |
| `approval` | `owner` (padrão): o plano espera por `approve`. `auto`: o plano é aprovado automaticamente após a revisão e o pipeline começa de imediato; o diário registra que o plano foi aprovado automaticamente |
| `limits` | `stage_files` e `stage_lines`: os limites de uma etapa (10 e 500) |
| `worktree_setup` | Comandos que o Friday executa em um novo worktree antes de planejar (dependências e similares): arrays de argumentos com as substituições `{repository}` e `{worktree}`; precisam ser repetíveis. Exemplo para npm no macOS: `["sh", "-c", "[ -e node_modules ] \|\| cp -cR {repository}/node_modules node_modules"]` |
| `executor_commands` | Comandos que o executor pode rodar sozinho, na forma exata: `"python3 -m pytest"`, não `python` |
| `executor_deny_read` | Caminhos adicionais que o shell do executor não pode ler |
| `milestone_rounds` | Quantas rodadas do executor um marco recebe (4) |
| `gate`, `gate_image`, `gate_mode`, `gate_*` | O comando de verificação no Docker; `git-diff` compara com a base da tarefa |
| `environment_errors` | Padrões de linhas de log que indicam falha de ambiente, não falha de código |
| `browser`, `preview`, `behavior_test_policy` | O ambiente de teste, o preview e as regras da aceitação no navegador |
| `tracker` | Yandex Tracker (API ou MCP), usado para ler a descrição da tarefa |
| `journal` | Onde gravar o diário da tarefa: `[{"kind": "markdown", "dir": "..."}, {"kind": "jsonl", "dir": "..."}]`; `journal_dir` é uma forma curta para um único diário markdown |
| `delivery` | `{"transport": "github" \| "gitlab" \| "none", "target_branch": "..."}` e a chave `github.repository` ou `gitlab.base_url` + `gitlab.project` |

### Modelos e papéis

```json
"models": {
  "fable": {"model": "claude-fable-5-1", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "strength": 1, "scarce": true, "effort": "high"},
  "opus-high": {"model": "claude-opus-5-5", "vendor": "anthropic", "driver": "claude", "tier": "frontier", "effort": "high"},
  "glm-high": {"model": "glm-5.3", "vendor": "zai", "driver": "claude", "tier": "strong", "effort": "high",
               "token_file": "~/.config/friday/zai.env", "token_key": "ZAI_API_KEY"}
},
"roles": {"plan": ["fable"], "develop": ["glm-high"], "review": ["opus-high"]}
```

- `driver`: o que executa o modelo, `claude` ou `codex`. A Z.ai passa pelo Claude Code; o token é lido de um arquivo que só o dono pode acessar.
- `tier`: `frontier`, `strong` ou `efficient`; `strength` ordena os modelos dentro de um nível.
- `effort`: a profundidade do raciocínio (`low` … `max`); um mesmo modelo pode ser descrito várias vezes com esforços diferentes.
- `scarce`: um modelo caro; é usado uma vez por tarefa se as regras permitirem que a tarefa siga sem ele.
- `price`: o preço por milhão de tokens por tipo (`input`, `output`, `cache_read`, `cache_write`), usado para estimar o gasto quando o fornecedor não informa o preço.

Papéis: `plan`, `plan-review`, `develop`, `review`, `final-review`, `blind-code`, `browser`. As listas em `roles` são preferências: se a lista não tem um modelo adequado, o Friday escolhe outro do catálogo. Regras rígidas: só modelos `frontier` revisam código, o revisor não é mais fraco que o autor do trabalho, e a verificação cega não é feita pelo autor do código. Regras flexíveis, em ordem: não o mesmo modelo do autor; um modelo caro uma única vez; um fornecedor diferente; a verificação cega não feita pelo autor do plano. Uma regra flexível só pode ser quebrada quando não há outra saída, e a violação é registrada no histórico da tarefa.

Um exemplo de atribuição de papéis está em [examples/models.json](../../examples/models.json).

## Diário

O card da tarefa é a única fonte da verdade. Os diários o refletem e se atualizam após eventos importantes: o plano ficou pronto e foi aprovado, um marco foi concluído ou falhou, uma decisão do dono, a revisão final, o MR, uma interrupção. Para atualizá-los manualmente, execute `friday journal KEY`.

- `markdown`: uma página `KEY.md` e uma página de visão geral compartilhada que lista todas as tarefas. Para o Obsidian, defina a pasta do vault: as páginas têm front matter (chave, estado, gasto, a tag `friday`), e as tarefas são ligadas por wiki links. A página de uma tarefa contém o plano com suas premissas e achados não resolvidos, os marcos com o resumo do executor e os commits, os achados contestados, as decisões, o gasto por papel e a linha do tempo.
- `jsonl`: todos os eventos da tarefa, para análise em planilhas ou dashboards.

Os templates são Markdown com as substituições `{{key}}`, `{{state}}`, `{{status}}`, `{{plan}}`, `{{milestones}}`, `{{disputes}}`, `{{decisions}}`, `{{costs}}`, `{{timeline}}`, `{{notes}}`. O Friday usa o template do campo `template` do destino, depois `.friday/task.md` no repositório do projeto, depois o embutido ([friday/templates](../../friday/templates)). O texto entre `<!-- friday:notes -->` e `<!-- /friday:notes -->` é seu: ele é preservado a cada atualização. O Friday não sobrescreve uma página que ele não escreveu.

## Configurações da máquina

`FRIDAY_HOME/machine.json` define os recursos compartilhados por todas as tarefas:

```json
{"slots": {"gate": 2, "browser": 2, "vendor:zai": 2}, "ports": [5200, 5399]}
```

Por padrão, rodam ao mesmo tempo um gate e dois navegadores, e os fornecedores não têm limite. As cotas dos fornecedores são acompanhadas automaticamente: uma execução que atinge um limite registra quando a cota volta e, até lá, o fornecedor é ignorado na escolha dos modelos.

## Limites

- **Sandbox do executor.** O shell do executor (Claude Code) roda em um sandbox: sem rede, sem leitura de `~/.ssh`, `~/.aws`, `~/.config` e similares, escrita apenas no worktree e em uma pasta temporária, e sem execução fora do sandbox. Comandos que não estão em `executor_commands` e não são comandos do git são recusados. O Codex roda em seu próprio sandbox, com os mesmos limites.
- **Os revisores** apenas leem. A verificação cega roda em uma pasta vazia.
- **O gate** roda em um contêiner sem acesso à rede, sobre um snapshot do commit montado como somente leitura.
- **O navegador** recebe apenas as ferramentas do Friday para um preview; contornar a autorização e ler os cabeçalhos de requisições autorizadas são proibidos.
- **Publicação.** O Friday faz push apenas da branch da própria tarefa e abre apenas um MR/PR em rascunho. Ele não sobrescreve a branch de outra pessoa, não faz merge nem deploy.
- **Ainda não verificado ao vivo:** os papéis no Codex, a aceitação no navegador e a publicação no GitLab foram verificados apenas por testes com fakes.
