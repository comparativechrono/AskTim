"""The models an AI service offers, and short names for them (`AskTim! model opus`).

Each service lists the models that a key may use: Anthropic and OpenAI-compatible services at
GET /v1/models, Gemini at GET /v1beta/models. A short name - opus, sonnet or haiku for Claude,
pro, flash or flash-lite for Gemini - means the newest model of that family in the list. When the
list cannot be had (no key yet, no internet), a short table of current models is used instead.
"""

import re

from . import config as _config
from . import net
from .providers import ProviderError

# the newest model of each family when the service's own list cannot be had
SHORT = {
    "claude": {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"},
    "gemini": {"flash": "gemini-3.8-flash", "flash-lite": "gemini-3.5-flash-lite"},
    "openai": {},
}

# what each Claude family is for, for lists and the settings window
ABOUT = {
    "opus": "the most capable; slower and dearer",
    "sonnet": "fast and capable: AskTim!'s default",
    "haiku": "the fastest and cheapest",
    "pro": "Gemini's most capable",
    "flash": "fast: AskTim!'s default for Gemini",
    "flash-lite": "the fastest and cheapest",
}


class Model(object):
    def __init__(self, id, name="", created=""):
        self.id = id
        self.name = name or id
        self.created = created or ""

    def family(self):
        """opus, sonnet, haiku, pro, flash, flash-lite ... or ''."""
        for word in ("flash-lite", "opus", "sonnet", "haiku", "flash", "pro"):
            if re.search(r"(^|-)%s(-|$)" % re.escape(word), self.id):
                return word
        return ""

    def __repr__(self):
        return "Model(%r)" % self.id


def _error(e, label):
    if e.network:
        return ProviderError("Could not reach %s to list its models: %s" % (label, e), kind="network")
    msg = e.body or str(e)
    try:
        from .providers import error_message_from_body

        msg = error_message_from_body(e.body) if e.body else str(e)
    except Exception:
        pass
    if e.status in (401, 403):
        return ProviderError("%s did not accept the key (%s)." % (label, msg), kind="auth", status=e.status)
    if e.status == 404:
        return ProviderError("%s does not list its models (HTTP 404). Type the model's name instead." % label, kind="notfound", status=404)
    return ProviderError("%s could not list its models (HTTP %s: %s)." % (label, e.status, msg), kind="other", status=e.status)


def _version(model_id):
    m = re.search(r"-(\d+(?:[.-]\d+)*)", model_id)
    if not m:
        return ()
    return tuple(int(x) for x in re.split(r"[.-]", m.group(1)) if x.isdigit())


def list_models(cfg, provider=None, key=None, timeout=20):
    """The models the key may use, newest first. Raises ProviderError."""
    name = _config.normalise_provider(provider) or cfg.provider
    if key is None:
        key, _where = _config.find_key(name)
    label = {"claude": "Anthropic", "gemini": "Google Gemini", "openai": "The OpenAI-compatible service"}[name]
    if name == "claude":
        if not key:
            raise ProviderError("There is no Claude key yet.", kind="config")
        base = (cfg.get("anthropic_base_url") or "https://api.anthropic.com").rstrip("/")
        headers = {"x-api-key": key, "anthropic-version": cfg.get("anthropic_version") or "2023-06-01"}
        out, after = [], None
        for _page in range(10):
            url = base + "/v1/models?limit=1000" + ("&after_id=" + after if after else "")
            try:
                data = net.get_json(url, headers, timeout=timeout)
            except net.HTTPError as e:
                raise _error(e, label)
            items = data.get("data") or []
            out += [Model(m.get("id", ""), m.get("display_name", ""), m.get("created_at", "")) for m in items if m.get("id")]
            if not data.get("has_more") or not items:
                break
            after = items[-1].get("id")
        # (the service lists the newest first; keep that order, but make sure of it where dates are given)
        if all(m.created for m in out):
            out.sort(key=lambda m: m.created, reverse=True)
        return out
    if name == "gemini":
        if not key:
            raise ProviderError("There is no Gemini key yet.", kind="config")
        base = (cfg.get("gemini_base_url") or "https://generativelanguage.googleapis.com").rstrip("/")
        out, token = [], None
        for _page in range(10):
            url = base + "/v1beta/models?pageSize=1000" + ("&pageToken=" + token if token else "")
            try:
                data = net.get_json(url, {"x-goog-api-key": key}, timeout=timeout)
            except net.HTTPError as e:
                raise _error(e, label)
            for m in data.get("models") or []:
                ident = str(m.get("name", "")).replace("models/", "", 1)
                if ident and "generateContent" in (m.get("supportedGenerationMethods") or ["generateContent"]):
                    out.append(Model(ident, m.get("displayName", "")))
            token = data.get("nextPageToken")
            if not token:
                break
        # newest version first; within a version, the plain name before dated and preview variants
        out.sort(key=lambda m: (_version(m.id), -len(m.id)), reverse=True)
        return out
    base = (cfg.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
    try:
        data = net.get_json(base + "/models", {"authorization": "Bearer " + key} if key else {}, timeout=timeout)
    except net.HTTPError as e:
        raise _error(e, label)
    items = data.get("data") or data.get("models") or []
    out = [Model(str(m.get("id") or m.get("name") or ""), "", str(m.get("created") or "")) for m in items if isinstance(m, dict)]
    out = [m for m in out if m.id]
    out.sort(key=lambda m: (m.created.isdigit() and int(m.created) or 0), reverse=True)
    return out


def is_short_name(word):
    return bool(re.match(r"^[a-z]+(-[a-z]+)?$", (word or "").strip().lower()))


def resolve(cfg, word, provider=None, available=None):
    """(model id, how it was found) for what the user typed: a short name (opus, flash ...) becomes
    the newest model of that family; anything else is kept as it is. `available`: the service's
    list, if it has already been fetched (None: fetch it; False: do not). Raises ValueError when a
    short name matches no model."""
    name = _config.normalise_provider(provider) or cfg.provider
    word = (word or "").strip()
    low = word.lower()
    if name == "openai" or not is_short_name(low):
        return word, "as given"  # (a local service's names, such as mistral or llama3, are its own)
    if available is None:
        try:
            available = list_models(cfg, name)
        except ProviderError:
            available = False
    if available:
        fam = [m for m in available if m.family() == low] or [m for m in available if re.search(r"(^|-)%s(-|$)" % re.escape(low), m.id)]
        if name == "gemini" and fam:
            # the plain name (gemini-3.8-flash) before previews and dated copies
            plain = [m for m in fam if re.fullmatch(r"gemini-[\d.]+-%s" % re.escape(low), m.id)]
            fam = plain or fam
        if fam:
            return fam[0].id, "the newest %s model that the service lists" % low
    table = SHORT.get(name, {})
    if low in table:
        return table[low], "AskTim!'s list of current models"
    other = [p for p in ("claude", "gemini") if p != name and low in SHORT[p]]
    hint = " (%s is a %s model: AskTim! use %s switches to it)" % (low, _config.PROVIDER_NAMES[other[0]], other[0]) if other else ""
    if available:
        raise ValueError("%s lists no model called %s%s." % (_config.PROVIDER_NAMES[name], word, hint))
    raise ValueError("AskTim! knows no %s model called %s%s. Short names: %s." % (
        _config.PROVIDER_NAMES[name], word, hint, ", ".join(sorted(SHORT.get(name, {})))))
