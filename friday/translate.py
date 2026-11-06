"""Translate what agents wrote, always in English, into the language a person reads (and an owner's words into English).

An inexpensive model does it in one structured run per batch; translations are cached by text and language, so a
repeated status, plan or journal costs nothing. A failure never stops anything: the original text is shown instead."""
import hashlib
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import card as cards
from . import i18n, models, resources, runner
from .errors import FridayError
from .i18n import N_
from .locks import locked

BATCH = 60
SLOT_WAIT = 30
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["translations"],
          "properties": {"translations": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": ["id", "text"],
              "properties": {"id": {"type": "integer"}, "text": {"type": "string"}}}}}}
RULES = (
    "Translate each text below into {language}. They come from a development pipeline: plans, code reviews and their "
    "findings, a merge request. Keep code, file paths, identifiers, commands, URLs, model names, task keys and Markdown "
    "exactly as they are, and keep the meaning and tone; do not add or drop anything. {terms}Return one translation per "
    "id. Do not read or edit any file.")
# The pipeline's own words, translated by the catalogs so that a translation uses the same terms as the interface.
TERMS = (N_("milestone"), N_("stage"), N_("gate"), N_("plan"), N_("review"), N_("blind check"), N_("executor"),
         N_("owner"), N_("criterion"), N_("draft merge request"), N_("dispute"))


def cache_path(root, lang):
    return Path(root) / "translations" / (lang + ".json")


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def cached(root, lang):
    try:
        value = json.loads(cache_path(root, lang).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def remember(root, lang, found):
    with locked(root, "translations:" + lang):
        value = dict(cached(root, lang), **found)
        path = cache_path(root, lang)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        temporary.replace(path)


def needs(text, lang):
    """English text needs no translation into English; anything else with letters does."""
    if not isinstance(text, str) or not text.strip():
        return False
    if lang == "en":
        return any(ord(char) > 127 and char.isalpha() for char in text)
    return any(char.isalpha() for char in text)


def model(root, profile, key=None):
    """The translator: the task's cast one, else the catalog's least expensive available model."""
    if key is not None:
        try:
            entry = cards.read(root, key).get("cast", {}).get("translate")
            if entry:
                return entry
        except FridayError:
            pass
    entry, _ = models.choose(profile, "translate", available=models.availability(root))
    return models.public(entry)


def prompt(lang, items):
    terms = ""
    if lang != "en":
        pairs = ["%s → %s" % (term, i18n.tr(lang, term)) for term in TERMS if i18n.tr(lang, term) != term]
        terms = "Use these terms: " + "; ".join(pairs) + ". " if pairs else ""
    language = "English" if lang == "en" else i18n.NAMES[lang]
    return (RULES.format(language=language, terms=terms) + "\n\n" +
            json.dumps([{"id": index, "text": text} for index, text in enumerate(items)], ensure_ascii=False, indent=1))


def run(root, key, profile, lang, items):
    entry = model(root, profile, key)
    with tempfile.TemporaryDirectory(prefix="friday-translate-") as empty:
        logs = (cards.directory(root, key) / "runs" if key else Path(empty)) / ("translate-" + uuid.uuid4().hex[:8])
        spec = runner.Spec(driver=entry["driver"], model=entry["model"], effort=entry.get("effort"), prompt=prompt(lang, items),
                           cwd=empty, log_dir=str(logs), schema=SCHEMA, env=models.environment(entry, profile),
                           idle_timeout=300, total_timeout=900)
        started = time.time()
        # A journal is written while the pipeline runs: wait briefly for the vendor, then show the English text instead.
        with resources.slot(root, "vendor:" + entry["vendor"], timeout=SLOT_WAIT):
            result = runner.run(spec)
    if key:
        usage = dict(result.usage)
        estimate = models.estimate(entry, profile, usage) if usage.get("cost_usd") is None else None
        if estimate is not None:
            usage.update(cost_usd=estimate, cost_estimated=True)
        cards.record(root, key, "run_finished", role="translate", model=entry["model"], driver=entry["driver"],
                     vendor=entry["vendor"], tier=entry.get("tier"), status=result.status, usage=usage,
                     duration_ms=result.duration_ms or int((time.time() - started) * 1000), error=result.error)
    if result.status == "quota":
        models.exhausted(root, entry["vendor"], result.error, result.limits)
    if result.status != "ok" or not isinstance(result.structured, dict):
        raise FridayError("translation_failed", "Translation did not return a result ({status}).", status=result.status)
    found = {}
    for item in result.structured.get("translations", []):
        if isinstance(item.get("id"), int) and 0 <= item["id"] < len(items) and isinstance(item.get("text"), str) and item["text"]:
            found[fingerprint(items[item["id"]])] = item["text"]
    return found


def mapping(root, key, profile, texts, lang):
    """{text: translation} for the given texts; texts that could not be translated map to themselves."""
    wanted = sorted({text for text in texts if needs(text, lang)})
    result = {text: text for text in texts if isinstance(text, str)}
    if not wanted:
        return result
    known = cached(root, lang)
    missing = [text for text in wanted if fingerprint(text) not in known]
    try:
        for start in range(0, len(missing), BATCH):
            found = run(root, key, profile, lang, missing[start:start + BATCH])
            remember(root, lang, found)
            known.update(found)
    except (FridayError, OSError) as exc:
        print(i18n._("Friday: translation unavailable, showing the original text: {reason}", reason=i18n.error(exc)), file=sys.stderr)
    for text in wanted:
        result[text] = known.get(fingerprint(text), text)
    return result


def into(root, key, profile, lang):
    """A function that translates one text, after the texts it will be asked for were given to prefetch."""
    table = {}
    def prefetch(texts):
        if lang != "en":
            table.update(mapping(root, key, profile, list(texts), lang))
    def one(text):
        return table.get(text, text) if isinstance(text, str) else text
    one.prefetch = prefetch
    return one


def to_english(root, key, profile, text):
    """An owner's words in English for the agents; the original when it is English already or translation fails."""
    if not needs(text, "en"):
        return text
    return mapping(root, key, profile, [text], "en").get(text, text)
