<!-- i18n: source=GLOSSARY.md sha256=fd78b7d8bdb2 -->
# Friday

[English](../../GLOSSARY.md) · [Русский](../ru/GLOSSARY.md) · [简体中文](../zh-cn/GLOSSARY.md) · [日本語](../ja/GLOSSARY.md) · [한국어](../ko/GLOSSARY.md) · [Português (Brasil)](../pt-br/GLOSSARY.md) · **Español** · [Deutsch](../de/GLOSSARY.md) · [Français](../fr/GLOSSARY.md) · [Bahasa Indonesia](../id/GLOSSARY.md)

_Esta es una traducción de la documentación en inglés; si hay diferencias, prevalece la versión en inglés._

Un pipeline de desarrollo personal: lleva una tarea desde el plan hasta un MR en borrador, usando modelos de distintos proveedores y comprobando su trabajo.

## Trabajo

**Tarea**:
Una unidad de trabajo del propietario, con su propia ficha, rama y worktree; normalmente corresponde a un ticket del tracker.
_Avoid_: trabajo, encargo

**Plan**:
La lista de hitos y etapas de una tarea, con criterios de aceptación, aprobada por el propietario.

**Hito**:
Un grupo de 2–5 etapas consecutivas que pasa el gate y la revisión como un todo.
_Avoid_: fase

**Etapa**:
La parte más pequeña de un plan; termina con un único commit.
_Avoid_: paso

**Criterio**:
Una condición de aceptación verificable de una etapa; es un criterio de código (se comprueba contra el diff) o un criterio de comportamiento (se observa en el navegador).

**Perfil**:
El archivo JSON que configura Friday para un proyecto (repositorio, modelos, roles, gate, navegador, tracker, diario, entrega); puede extender otro perfil mediante la clave `extends`.

## Roles

**Propietario**:
La persona que aprueba el plan, acepta el MR en borrador y resuelve los hallazgos en disputa.
_Avoid_: operador, usuario

**Planificador**:
El modelo que escribe el plan.

**Ejecutor**:
El modelo que escribe el código de un hito.
_Avoid_: desarrollador

**Revisor**:
El modelo que revisa el plan, un hito o el diff final y emite un veredicto sobre los criterios.

**Nivel del modelo**:
La clase de capacidad de un modelo: `frontier`, `strong` o `efficient`.

**Asignación de roles**:
La asignación de modelos concretos a los roles de una tarea, fijada antes de aprobar el plan.

## Comprobaciones

**Gate**:
Comprobaciones deterministas sin modelos, ejecutadas en un contenedor aislado sobre un commit concreto.
_Avoid_: CI, validación

**Revisión del hito**:
Una revisión del diff del hito, con el log del gate, hecha por un revisor de otro proveedor.

**Revisión final**:
Una revisión de todo el diff de la tarea antes del MR, que incluye la comprobación a ciegas del código.

**Aceptación en el navegador**:
Observación de los criterios de comportamiento de un hito en el navegador, sobre un entorno de pruebas local.
_Avoid_: blind-behavior

**Hallazgo en disputa**:
Un hallazgo de un revisor con el que el ejecutor no estuvo de acuerdo y que se envió al propietario a través del diario.

## Registros

**Ficha**:
El estado de una tarea para la máquina en Friday: eventos, hitos, veredictos, gasto.

**Diario**:
El historial legible de una tarea en Obsidian, generado a partir de la ficha con plantillas.
