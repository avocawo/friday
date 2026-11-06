<!-- i18n: source=README.md sha256=a008f1f30688 -->
<p align="center">
  <img src="../../assets/friday-logo.png" alt="Friday — logo em neon" width="900">
</p>

<h1 align="center">Friday</h1>

<p align="center"><strong>Um pipeline pessoal de desenvolvimento que aproxima a sexta-feira.</strong><br>
Da tarefa ao MR em rascunho: modelos fortes planejam e revisam, modelos econômicos escrevem o código.</p>

<p align="center">Python 3.11+ · macOS / Linux · <a href="../../LICENSE">MIT</a> · versão inicial</p>

[English](../../README.md) · [Русский](../ru/README.md) · [简体中文](../zh-cn/README.md) · [日本語](../ja/README.md) · [한국어](../ko/README.md) · **Português (Brasil)** · [Español](../es/README.md) · [Deutsch](../de/README.md) · [Français](../fr/README.md) · [Bahasa Indonesia](../id/README.md)

_Esta é uma tradução da documentação em inglês; em caso de divergência, vale a versão em inglês._

---

```text
tarefa → plano (modelo forte) → revisão do plano (outro fornecedor) → sua aprovação
       → marcos: executor em sandbox → gate → revisão do marco → aceitação da UI no navegador
       → revisão final + verificação cega → MR em rascunho
```

Você participa duas vezes: aprova o plano e aceita o MR em rascunho. Entre esses dois pontos o pipeline não para: os achados contestados da revisão chegam a você junto com o MR.

## Pontos principais

- **Modelos por nível, não por nome.** O perfil tem um catálogo de modelos com o fornecedor e o nível (`frontier`, `strong`, `efficient`) de cada um. Só modelos `frontier` revisam código, o revisor nunca é mais fraco que o autor e, sempre que possível, o revisor é de outro fornecedor. Um modelo caro pode ser marcado para rodar uma única vez por tarefa.
- **Um executor econômico com feedback rápido.** O executor roda sozinho o linter, a checagem de tipos e os testes em um sandbox sem acesso à rede, e o gate entrega a ele apenas as verificações que falharam.
- **Você vê o que está acontecendo.** `friday status` mostra a fase, o modelo, há quanto tempo o agente está em silêncio e quanto foi gasto em cada tarefa.
- **Várias tarefas ao mesmo tempo.** Cada tarefa tem a sua própria branch e o seu próprio worktree. As configurações da máquina definem quantos gates, navegadores e execuções de um mesmo fornecedor podem rodar simultaneamente.

## Início rápido

Você precisa de Python 3.11+, Git e do Claude Code (o Codex CLI é opcional). O gate e a aceitação no navegador também precisam do Docker.

```sh
uv tool install git+https://github.com/avocawo/friday   # or: pipx install git+https://github.com/avocawo/friday
mkdir -p ~/.config/friday/profiles && cd ~/.config/friday/profiles
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/models.json
curl -fsSLO https://raw.githubusercontent.com/avocawo/friday/main/examples/project-profile.json
# em project-profile.json, defina o repository, o worktree_root e os executor_commands do seu projeto
export FRIDAY_PROFILE=~/.config/friday/profiles/project-profile.json
friday doctor
friday start TASK-1 --brief task.md
friday approve TASK-1
friday status
```

O Friday fala dez idiomas: `friday --lang ru …`, `FRIDAY_LANG=ru` ou `friday config language ru`. Os agentes sempre trabalham em inglês; o que você lê é traduzido. Exemplos de perfis para projetos Node e Python estão em [examples/profiles](../../examples/profiles).

## Comandos

| Comando | O que faz |
| --- | --- |
| `start KEY --profile P [--brief F]` | Cria o worktree e o plano, e pede a outro modelo que revise o plano |
| `plan KEY` | Mostra o plano com os achados não resolvidos |
| `approve KEY [--no-run]` | Aprova o plano e inicia o pipeline em segundo plano |
| `status [KEY] [--json]` | Mostra o que as tarefas estão fazendo e quanto foi gasto |
| `decide KEY name "text" [--reopen M2]` | Registra uma decisão do dono; reabre apenas os marcos indicados |
| `run KEY` / `resume KEY` / `stop KEY` | Executa neste terminal, continua, interrompe |
| `archive KEY [--force]` | Remove o worktree e o card; a branch permanece |
| `journal KEY` | Atualiza os diários das tarefas (Obsidian, jsonl) |
| `doctor --profile P` | Verifica se a máquina e o perfil estão prontos |
| `config language L` | Define o idioma em que o Friday fala nesta máquina |

Para detalhes sobre o perfil, os modelos e os limites, veja o [guia](guide.md). Para entender por que o pipeline foi construído assim, veja a [análise do piloto](../process-redesign.md) (em russo) e os [ADRs](../adr/) (0001–0006 em russo).

## Desenvolvimento

```sh
git clone https://github.com/avocawo/friday.git && cd friday
python3 -m unittest discover -s tests
uvx ruff check .
python3 -m friday.i18n
```

Os testes usam dados temporários e agentes falsos, sem rede nem Docker. A organização do código está em [docs/architecture.md](../architecture.md); como propor uma mudança está em [CONTRIBUTING.md](../../CONTRIBUTING.md).

[Guia](guide.md) · [Glossário](GLOSSARY.md) · [Contribuindo](../../CONTRIBUTING.md) · [Licença](../../LICENSE)
