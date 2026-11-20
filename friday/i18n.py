"""The language people read Friday in. Agents always work in English; this only renders what a person sees.

Messages are keyed by their English text, so English needs no catalog and a missing translation falls back to it.
friday/locales/<language>.json maps an English message to its translation, or a plural message to the list of its
forms in the language's plural order. `python -m friday.i18n` lists what each catalog misses or no longer needs."""
import ast
import json
import os
import string
import sys
from pathlib import Path

LANGUAGES = ("en", "ru", "zh-cn", "ja", "ko", "pt-br", "es", "de", "fr", "id")
NAMES = {"en": "English", "ru": "Русский", "zh-cn": "简体中文", "ja": "日本語", "ko": "한국어", "pt-br": "Português (Brasil)",
         "es": "Español", "de": "Deutsch", "fr": "Français", "id": "Bahasa Indonesia"}
LOCALES = Path(__file__).resolve().parent / "locales"
SOURCES = Path(__file__).resolve().parent
ALIASES = {"zh": "zh-cn", "zh-hans": "zh-cn", "zh-sg": "zh-cn", "pt": "pt-br", "in": "id"}

_selected = None
_catalogs = {}


def normalize(value):
    """A supported language for a locale-like value (ru_RU.UTF-8, pt-BR, zh_CN), or None."""
    if not isinstance(value, str) or not value or value in ("C", "POSIX"):
        return None
    tag = value.split(".")[0].split("@")[0].replace("_", "-").lower()
    if tag in LANGUAGES:
        return tag
    if tag in ALIASES:
        return ALIASES[tag]
    base = tag.split("-")[0]
    return base if base in LANGUAGES else ALIASES.get(base)


def config_path(root=None):
    from . import state
    return Path(root or state.home()) / "config.json"


def saved(root=None):
    try:
        value = json.loads(config_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value.get("language") if isinstance(value, dict) else None


def save(language, root=None):
    """Remember the interface language for this FRIDAY_HOME; None forgets it."""
    path = config_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    if language is None:
        value.pop("language", None)
    else:
        value["language"] = language
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def resolve(explicit=None, root=None):
    """--lang, then FRIDAY_LANG, then the saved choice, then the system locale, then English."""
    for value in (explicit, os.environ.get("FRIDAY_LANG"), saved(root)):
        if value:
            return normalize(value) or "en"
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(name):
            return normalize(os.environ[name]) or "en"
    return "en"


def select(language):
    global _selected
    _selected = normalize(language) or "en"
    return _selected


def language():
    return _selected or select(resolve())


def output_language(profile=None):
    """What others read (the MR, the shared journal): the profile's language, else the person's."""
    return normalize((profile or {}).get("language")) or language()


def catalog(lang):
    if lang not in _catalogs:
        try:
            _catalogs[lang] = json.loads((LOCALES / (lang + ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _catalogs[lang] = {}
    return _catalogs[lang]


def fill(text, params):
    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        return text


def tr(lang, text, /, **params):
    """The message in a given language."""
    value = catalog(lang).get(text) if lang != "en" else None
    return fill(value if isinstance(value, str) and value else text, params)


def _(text, /, **params):
    """The message in the person's language."""
    return tr(language(), text, **params)


def plural_forms(lang):
    return 1 if lang in ("ja", "ko", "zh-cn", "id") else 3 if lang == "ru" else 2


def plural_index(lang, count):
    if lang in ("ja", "ko", "zh-cn", "id"):
        return 0
    if lang == "ru":
        if count % 10 == 1 and count % 100 != 11:
            return 0
        return 1 if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14 else 2
    if lang in ("fr", "pt-br"):
        return 0 if count in (0, 1) else 1
    return 0 if count == 1 else 1


def ntr(lang, singular, plural, count, /, **params):
    params = dict(params, n=count)
    forms = catalog(lang).get(singular) if lang != "en" else None
    if isinstance(forms, list) and forms:
        return fill(forms[min(plural_index(lang, count), len(forms) - 1)], params)
    return fill(singular if count == 1 else plural, params)


def ngettext(singular, plural, count, /, **params):
    return ntr(language(), singular, plural, count, **params)


def N_(text):
    """Mark a message for the catalogs where it is defined; it is translated where it is shown."""
    return text


class Message(str):
    """English text that remembers its template, so that it is stored in English and shown in the person's language."""

    def __new__(cls, template, **params):
        value = super().__new__(cls, fill(template, params))
        value.template, value.params = template, params
        return value


def message(template, /, **params):
    return Message(template, **params)


def show(value):
    """A Message in the person's language; any other text as it is."""
    if not isinstance(value, Message):
        return value
    return _(value.template, **{name: show(param) for name, param in value.params.items()})


def error(exc):
    """A Friday error in the person's language: its template translated, or its English text."""
    template = getattr(exc, "template", None)
    if template is None:
        return str(exc)
    return _(template, **getattr(exc, "params", {}))


# Extraction: the messages the code uses, for the catalogs and their test.

CALLS = {"_": 0, "N_": 0, "say": 0, "tr": 1, "ngettext": 0, "ntr": 1, "FridayError": 1, "message": 0}


def messages(paths=None):
    """{message: kind} for every literal passed to _, N_, say, tr, ngettext, ntr or FridayError in Friday's code."""
    found = {}
    files = paths or sorted(SOURCES.glob("*.py")) + [SOURCES.parent / "bin" / "friday"]
    for path in files:
        if not Path(path).is_file():
            continue
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else None
            if name not in CALLS or len(node.args) <= CALLS[name]:
                continue
            argument = node.args[CALLS[name]]
            if not (isinstance(argument, ast.Constant) and isinstance(argument.value, str)):
                continue
            found.setdefault(argument.value, "plural" if name in ("ngettext", "ntr") else "text")
    return found


def placeholders(text):
    return sorted({name for _, name, _, _ in string.Formatter().parse(text) if name})


def check(lang, found=None):
    """Problems of one catalog: missing and stale messages, wrong placeholders, wrong plural forms."""
    found = messages() if found is None else found
    values = catalog(lang)
    problems = []
    for text, kind in found.items():
        if text not in values:
            problems.append("missing: " + text)
            continue
        value = values[text]
        forms = value if isinstance(value, list) else [value]
        if kind == "plural" and (not isinstance(value, list) or len(value) != plural_forms(lang)):
            problems.append("plural forms: " + text)
        expected = placeholders(text) if kind != "plural" else sorted(set(placeholders(text)) | {"n"})
        for form in forms:
            if not isinstance(form, str) or not form:
                problems.append("empty: " + text)
            elif kind == "plural" and not set(placeholders(form)) <= set(expected):
                problems.append("placeholders: " + text)
            elif kind != "plural" and placeholders(form) != expected:
                problems.append("placeholders: " + text)
    problems += ["stale: " + text for text in values if text not in found]
    return problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    found = messages()
    if argv[:1] == ["--pending"]:
        lang = argv[1]
        values = catalog(lang)
        pending = {text: kind for text, kind in found.items() if text not in values}
        print(json.dumps(pending, ensure_ascii=False, indent=2))
        return 0
    failed = 0
    for lang in LANGUAGES[1:]:
        problems = check(lang, found)
        print("%s: %s" % (lang, "ok" if not problems else "%d problems" % len(problems)))
        for problem in problems[:20]:
            print("  " + problem)
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
