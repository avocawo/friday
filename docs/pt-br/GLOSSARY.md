<!-- i18n: source=GLOSSARY.md sha256=fd78b7d8bdb2 -->
# Friday

[English](../../GLOSSARY.md) · [Русский](../ru/GLOSSARY.md) · [简体中文](../zh-cn/GLOSSARY.md) · [日本語](../ja/GLOSSARY.md) · [한국어](../ko/GLOSSARY.md) · **Português (Brasil)** · [Español](../es/GLOSSARY.md) · [Deutsch](../de/GLOSSARY.md) · [Français](../fr/GLOSSARY.md) · [Bahasa Indonesia](../id/GLOSSARY.md)

_Esta é uma tradução da documentação em inglês; em caso de divergência, vale a versão em inglês._

Um pipeline pessoal de desenvolvimento: leva uma tarefa do plano até um MR em rascunho usando modelos de diferentes fornecedores e verifica o trabalho deles.

## Trabalho

**Tarefa**:
Uma unidade de trabalho do dono, com seu próprio card, branch e worktree; normalmente corresponde a um ticket do tracker.
_Avoid_: serviço, missão

**Plano**:
A lista de marcos e etapas de uma tarefa, com critérios de aceitação, aprovada pelo dono.

**Marco**:
Um grupo de 2 a 5 etapas consecutivas que passa pelo gate e pela revisão como um todo.
_Avoid_: fase, milestone

**Etapa**:
A menor parte de um plano; termina com um único commit.
_Avoid_: passo, estágio

**Critério**:
Uma condição de aceitação verificável de uma etapa; é um critério de código (verificado no diff) ou um critério de comportamento (observado no navegador).

**Perfil**:
O arquivo JSON que configura o Friday para um projeto (repositório, modelos, papéis, gate, navegador, tracker, diário, entrega); pode estender outro perfil pela chave `extends`.

## Papéis

**Dono**:
A pessoa que aprova o plano, aceita o MR em rascunho e resolve os achados contestados.
_Avoid_: operador, usuário

**Planejador**:
O modelo que escreve o plano.

**Executor**:
O modelo que escreve o código de um marco.
_Avoid_: desenvolvedor, programador

**Revisor**:
O modelo que revisa o plano, um marco ou o diff final e dá um veredito sobre os critérios.

**Nível do modelo**:
A classe de capacidade de um modelo: `frontier`, `strong` ou `efficient`.
_Avoid_: categoria, camada

**Atribuição de papéis**:
A designação de modelos específicos para os papéis de uma tarefa, fixada antes da aprovação do plano.

## Verificações

**Gate**:
Verificações determinísticas sem modelos, executadas em um contêiner isolado sobre um commit específico.
_Avoid_: CI, validação

**Revisão do marco**:
Uma revisão do diff do marco, com o log do gate, feita por um revisor de outro fornecedor.

**Revisão final**:
Uma revisão do diff inteiro da tarefa antes do MR, incluindo a verificação cega do código.

**Aceitação no navegador**:
Observação dos critérios de comportamento de um marco no navegador, em um ambiente de teste local.
_Avoid_: blind-behavior

**Achado contestado**:
Um achado do revisor com o qual o executor discordou e que foi enviado ao dono por meio do diário.
_Avoid_: problema discutido, ponto polêmico

## Registros

**Card**:
O estado de máquina da tarefa no Friday: eventos, marcos, vereditos, gasto.

**Diário**:
O histórico legível de uma tarefa no Obsidian, gerado a partir do card com templates.
_Avoid_: log, registro
