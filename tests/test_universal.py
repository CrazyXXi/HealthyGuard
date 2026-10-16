"""Brancher N'IMPORTE QUEL chatbot : fonction, commande, classe, profils, méthodes HTTP, diagnostic."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from healthy_guard.adapters.base import ChatbotAdapter, ChatResponse
from healthy_guard.adapters.config import build_chatbot, load_chatbots, validate_chatbot
from healthy_guard.adapters.plugin import (CallableAdapter, CommandAdapter, _split_command, load_callable, load_plugin)
from healthy_guard.adapters.web import AdapterError, RestBotAdapter
from healthy_guard.checkbot import ERROR, OK, WARNING, diagnose, hint_for, is_usable
from healthy_guard.cli import main
from healthy_guard.reporting.json_report import read_json

BOT_FILE = '''
from healthy_guard.adapters.base import ChatbotAdapter, ChatResponse

MEMOIRE = {}

def repondre(messages):
    return "Je suis une intelligence artificielle. Vous avez dit : " + messages[-1]["content"]

def avec_dict(messages):
    return {"reply": "ok", "extra": 1}

def mauvais_type(messages):
    return 42

class MonBot(ChatbotAdapter):
    def __init__(self, prefixe="Bot"):
        self.prefixe = prefixe

    def send(self, messages):
        return ChatResponse(f"{self.prefixe} : {messages[-1]['content']}")

class PasUnAdaptateur:
    pass
'''


@pytest.fixture()
def bot_file(tmp_path):
    path = tmp_path / "mon_bot.py"
    path.write_text(BOT_FILE, encoding="utf-8")
    return str(path)


def user(text):
    return {"role": "user", "content": text}


# --- fonction Python ---------------------------------------------------------------------------------------------

def test_a_python_function_becomes_a_chatbot(bot_file):
    adapter = load_callable(f"{bot_file}:repondre")
    reply = adapter.send([user("Bonjour")])
    assert reply.text == "Je suis une intelligence artificielle. Vous avez dit : Bonjour" and reply.latency_ms >= 0
    assert adapter.describe() == {"adapter": "CallableAdapter", "callable": f"{bot_file}:repondre"}


def test_return_types_are_normalised(bot_file):
    assert load_callable(f"{bot_file}:avec_dict").send([user("x")]).text == "ok"
    assert load_callable(f"{bot_file}:avec_dict").send([user("x")]).metadata == {"extra": 1}
    assert CallableAdapter(lambda m: ChatResponse("direct")).send([user("x")]).text == "direct"
    with pytest.raises(AdapterError, match="int"):
        load_callable(f"{bot_file}:mauvais_type").send([user("x")])


def test_the_function_cannot_alter_the_audit_history():
    seen = {}

    def fn(messages):
        messages[0]["content"] = "ALTÉRÉ"
        seen["n"] = len(messages)
        return "ok"

    history = [user("original")]
    CallableAdapter(fn).send(history)
    assert history[0]["content"] == "original"


@pytest.mark.parametrize("spec, message", [
    ("sans_deux_points", "module:nom"),
    ("fichier_absent.py:f", "introuvable"),
    ("module_qui_nexiste_pas_xyz:f", "Module introuvable"),
    ("C:\\x", "attendu"),
])
def test_bad_callable_specs_are_explained(spec, message):
    with pytest.raises(ValueError, match=message):
        load_callable(spec)


def test_a_missing_function_is_reported(bot_file):
    with pytest.raises(ValueError, match="n'existe pas"):
        load_callable(f"{bot_file}:absente")


# --- classe écrite par l'utilisateur ---------------------------------------------------------------------------------

def test_a_user_class_is_loaded_with_its_arguments(bot_file):
    adapter = load_plugin(f"{bot_file}:MonBot", {"prefixe": "Rasa"})
    assert isinstance(adapter, ChatbotAdapter) and adapter.send([user("hi")]).text == "Rasa : hi"


def test_only_chatbot_adapters_are_accepted_as_plugins(bot_file):
    with pytest.raises(ValueError, match="hérite de ChatbotAdapter"):
        load_plugin(f"{bot_file}:PasUnAdaptateur")
    with pytest.raises(ValueError, match="arguments"):
        load_plugin(f"{bot_file}:MonBot", {"inconnu": 1})


# --- commande -----------------------------------------------------------------------------------------------------------

PY = sys.executable
ECHO = "import sys; print('echo:' + sys.stdin.read())"


def test_a_command_reads_the_last_message_on_stdin():
    adapter = CommandAdapter([PY, "-c", ECHO])
    history = [user("premier"), {"role": "assistant", "content": "r"}, user("dernier")]
    assert adapter.send(history).text == "echo:dernier"


def test_json_mode_sends_the_whole_conversation():
    code = "import sys, json; m = json.load(sys.stdin); print(len(m), m[0]['content'])"
    reply = CommandAdapter([PY, "-c", code], input_mode="json").send([user("a"), {"role": "assistant", "content": "b"}, user("c")])
    assert reply.text == "3 a"


def test_arg_mode_substitutes_placeholders_without_any_shell():
    code = "import sys; print(sys.argv[1])"
    adapter = CommandAdapter([PY, "-c", code, "{{message}}"], input_mode="arg")
    assert adapter.send([user("; echo piraté && whoami $(id) `x`")]).text == "; echo piraté && whoami $(id) `x`"


def test_the_session_is_stable_within_a_conversation_and_new_for_the_next_one():
    adapter = CommandAdapter([PY, "-c", "import os; print(os.environ['HG_SESSION'])"])
    first = adapter.send([user("a")]).text
    follow_up = adapter.send([user("a"), {"role": "assistant", "content": "r"}, user("b")]).text
    other = adapter.send([user("c")]).text
    assert first == follow_up and first != other and first.startswith("hg-")


def test_a_failing_command_is_a_technical_error_with_its_message():
    with pytest.raises(AdapterError, match=r"code 3.*pas content"):
        CommandAdapter([PY, "-c", "import sys; sys.stderr.write('pas content'); sys.exit(3)"]).send([user("x")])


def test_a_command_that_never_answers_times_out():
    with pytest.raises(AdapterError, match="Délai dépassé"):
        CommandAdapter([PY, "-c", "import time; time.sleep(5)"], timeout=0.5).send([user("x")])


def test_a_missing_program_is_reported():
    with pytest.raises(AdapterError, match="Impossible de lancer"):
        CommandAdapter(["programme_qui_nexiste_pas_xyz"]).send([user("x")])


def test_the_command_line_is_never_recorded_because_it_may_hold_a_secret():
    adapter = CommandAdapter([PY, "-c", ECHO, "--token", "SECRET-TOKEN-123"])
    description = json.dumps(adapter.describe())
    assert "SECRET-TOKEN-123" not in description and adapter.describe()["program"].lower().startswith("python")
    assert len(adapter.describe()["command_sha256"]) == 12


@pytest.mark.parametrize("command, expected", [
    ('prog --a "deux mots" b', ["prog", "--a", "deux mots", "b"]),
    ("prog simple", ["prog", "simple"]),
])
def test_command_strings_are_split_safely(command, expected):
    assert _split_command(command) == expected


def test_command_validation():
    with pytest.raises(ValueError, match="arg"):
        CommandAdapter("prog sans marqueur", input_mode="arg")
    with pytest.raises(ValueError, match="input_mode"):
        CommandAdapter("prog", input_mode="magie")
    with pytest.raises(ValueError, match="vide"):
        CommandAdapter([])


# --- méthodes HTTP, formulaires, texte brut -------------------------------------------------------------------------------

SEEN: list[dict] = []


class OddBots(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, body, content_type="text/plain; charset=utf-8", status=200):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        SEEN.append({"method": "GET", "path": url.path, "params": params})
        self._reply(f"GET texte brut : {params.get('q', '')}")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
        content_type = self.headers["Content-Type"]
        data = {k: v[0] for k, v in parse_qs(raw).items()} if "x-www-form-urlencoded" in content_type else json.loads(raw)
        SEEN.append({"method": "POST", "content_type": content_type, "data": data})
        self._reply(f"POST {content_type.split(';')[0]} : {data.get('q', '')}")

    do_PUT = do_POST


@pytest.fixture()
def odd_url(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    SEEN.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), OddBots)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_get_with_url_parameters_and_plain_text_reply(odd_url):
    adapter = RestBotAdapter(odd_url + "/ask", {"q": "{{message}}", "s": "{{session}}"}, "@text", method="GET")
    assert adapter.send([user('é & "="')]).text == 'GET texte brut : é & "="'
    assert SEEN[0]["params"]["q"] == 'é & "="' and SEEN[0]["params"]["s"].startswith("hg-")        # bien encodé dans l'URL


def test_form_body(odd_url):
    adapter = RestBotAdapter(odd_url + "/form", {"q": "{{message}}"}, "@text", body_format="form")
    assert adapter.send([user("a&b=c")]).text == "POST application/x-www-form-urlencoded : a&b=c"


def test_put_with_json_body(odd_url):
    reply = RestBotAdapter(odd_url + "/x", {"q": "{{message}}"}, "@text", method="PUT").send([user("salut")])
    assert reply.text == "POST application/json : salut" and SEEN[0]["data"] == {"q": "salut"}


def test_a_plain_text_bot_without_the_text_path_gets_a_helpful_hint(odd_url):
    with pytest.raises(AdapterError, match="pas du JSON") as error:
        RestBotAdapter(odd_url + "/x", {"q": "{{message}}"}, "reply").send([user("x")])
    assert "@text" in hint_for(str(error.value))


@pytest.mark.parametrize("kwargs, message", [({"method": "DELETE"}, "method"), ({"body_format": "xml"}, "body_format")])
def test_rest_options_are_validated(kwargs, message):
    with pytest.raises(ValueError, match=message):
        RestBotAdapter("http://x", {"q": "{{message}}"}, "@text", **kwargs)


# --- profils de chatbots ------------------------------------------------------------------------------------------------

def test_every_adapter_kind_can_be_built_from_a_profile(bot_file, monkeypatch):
    monkeypatch.setenv("BOT_KEY", "secret-valeur")
    profiles = {
        "llm": {"adapter": "openai", "model": "m", "base_url": "https://h.example", "api_key_env": "K", "extra_body": {"a": 1}},
        "claude": {"adapter": "anthropic", "model": "m"},
        "api": {"adapter": "http", "url": "http://x", "response_path": "r", "headers_env": {"X-Key": "BOT_KEY"}},
        "rasa": {"adapter": "rest", "url": "http://x", "body_template": {"message": "{{message}}"}, "response_path": "*.text",
                 "headers": {"A": "b"}, "method": "PUT", "body_format": "form"},
        "py": {"adapter": "python", "callable": f"{bot_file}:repondre"},
        "cmd": {"adapter": "command", "command": [PY, "-c", ECHO]} if False else {"adapter": "command", "command": f'"{PY}" -c "pass"'},
        "cls": {"adapter": "plugin", "class": f"{bot_file}:MonBot", "args": {"prefixe": "P"}},
    }
    built = {name: build_chatbot(profile) for name, profile in profiles.items()}
    assert {n: type(a).__name__ for n, a in built.items()} == {
        "llm": "OpenAICompatibleAdapter", "claude": "AnthropicAdapter", "api": "HttpJsonAdapter", "rasa": "RestBotAdapter",
        "py": "CallableAdapter", "cmd": "CommandAdapter", "cls": "MonBot"}
    assert built["api"].headers == {"X-Key": "secret-valeur"} and built["rasa"].method == "PUT"
    assert "secret-valeur" not in json.dumps(built["api"].describe())


@pytest.mark.parametrize("profile, message", [
    ("pas un objet", "objet JSON"),
    ({"adapter": "magie"}, "adapter"),
    ({"adapter": "openai"}, "requis"),
    ({"adapter": "openai", "model": "m", "mot_de_passe": "x"}, "inconnu"),
    ({"adapter": "rest", "url": "u", "response_path": "p"}, "body_template"),
    ({"adapter": "python"}, "callable"),
])
def test_invalid_profiles_are_rejected(profile, message):
    with pytest.raises(ValueError, match=message):
        validate_chatbot("x", profile)


def test_a_missing_secret_is_named_not_leaked(monkeypatch):
    monkeypatch.delenv("ABSENTE_XYZ", raising=False)
    with pytest.raises(ValueError, match="ABSENTE_XYZ"):
        build_chatbot({"adapter": "http", "url": "http://x", "headers_env": {"X": "ABSENTE_XYZ"}})


def test_profile_files_are_validated(tmp_path):
    path = tmp_path / "bots.json"
    path.write_text(json.dumps({"a": {"adapter": "python", "callable": "f.py:g"}}), encoding="utf-8")
    assert list(load_chatbots(path)) == ["a"]
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError, match="non vide"):
        load_chatbots(path)


# --- diagnostic -----------------------------------------------------------------------------------------------------------

class Scripted(ChatbotAdapter):
    """Chatbot de test : `memory` = garde le fil dans une conversation, `leaky` = mémoire globale entre conversations."""

    def __init__(self, memory=True, leaky=False, text="Bonjour !", fail=None, finish=None):
        self.memory, self.leaky, self.text, self.fail, self.finish = memory, leaky, text, fail, finish
        self.known = False

    def send(self, messages):
        if self.fail:
            raise self.fail
        if len(messages) == 1 and not self.leaky:
            self.known = False
        said = " ".join(m["content"] for m in messages).lower()
        if "kiwi" in said and "retiens" in said:
            self.known = True
        answer = "Votre fruit préféré est le kiwi." if (self.memory and self.known and "quel est mon fruit" in said) else self.text
        return ChatResponse(answer, latency_ms=3.0, metadata={"finish_reason": self.finish} if self.finish else {})


def levels(findings):
    return [f.level for f in findings]


def test_a_well_connected_chatbot_passes_every_check():
    findings = diagnose(Scripted())
    assert levels(findings) == [OK, OK, OK, OK] and is_usable(findings)


def test_a_chatbot_without_memory_gets_a_warning_not_a_failure():
    findings = diagnose(Scripted(memory=False))
    assert WARNING in levels(findings) and is_usable(findings)
    assert any("garder le fil" in f.title for f in findings)


def test_global_memory_between_conversations_is_flagged():
    findings = diagnose(Scripted(leaky=True))
    leak = next(f for f in findings if "nouvelle conversation connaît" in f.title)
    assert leak.level == WARNING and "canari" in leak.detail


@pytest.mark.parametrize("error, expected", [
    (AdapterError("HTTP 401 : proxy auth required"), "Authentification refusée"),
    (AdapterError("HTTP 404 : introuvable"), "Adresse introuvable"),
    (AdapterError("Chemin de réponse « a.b » introuvable (échec à « b »)."), "--response-path"),
    (AdapterError("HTTP 429 : quota"), "Quota"),
    (TimeoutError("timed out"), "--timeout"),
])
def test_connection_failures_come_with_the_likely_fix(error, expected):
    findings = diagnose(Scripted(fail=error))
    assert levels(findings) == [ERROR] and expected in findings[0].detail and not is_usable(findings)


def test_an_empty_or_truncated_reply_is_diagnosed():
    silent = diagnose(Scripted(text="   "))
    assert levels(silent) == [ERROR] and "muet" in silent[0].detail
    cut = diagnose(Scripted(text="", finish="length"))
    assert levels(cut) == [ERROR] and "--max-tokens" in cut[0].detail
    partial = diagnose(Scripted(text="Début de réponse", finish="length"))
    assert any("limite de longueur" in f.title and f.level == WARNING for f in partial)


def test_special_characters_that_break_the_bot_are_reported():
    class Fragile(Scripted):
        def send(self, messages):
            if "{accolades}" in messages[-1]["content"]:
                raise AdapterError("HTTP 400 : JSON invalide")
            return super().send(messages)

    findings = diagnose(Fragile())
    assert findings[-1].level == ERROR and "caractères spéciaux" in findings[-1].title


# --- via la ligne de commande --------------------------------------------------------------------------------------------------

def test_the_command_option_does_not_clash_with_sub_commands(bot_file, capsys):
    """Régression : --command (la commande du chatbot) écrasait le nom de la sous-commande et cassait tout le CLI."""
    assert main(["check-bot", "--adapter", "command", "--command", f'"{PY}" -c "print(1)"']) == 0
    assert "[OK]" in capsys.readouterr().out


def test_cli_check_bot_with_a_python_function(bot_file, capsys):
    assert main(["check-bot", "--adapter", "python", "--callable", f"{bot_file}:repondre"]) == 0
    assert "utilisable" in capsys.readouterr().out


def test_cli_check_bot_failure_explains_itself(capsys):
    code = main(["check-bot", "--adapter", "http", "--url", "http://127.0.0.1:9/x", "--max-retries", "0", "--timeout", "2"])
    out = capsys.readouterr().out
    assert code == 1 and "[ÉCHEC]" in out and "pas prêt" in out


def test_cli_audits_a_python_function_end_to_end(bot_file, tmp_path):
    out = tmp_path / "r.json"
    code = main(["audit", "--adapter", "python", "--callable", f"{bot_file}:repondre", "--pillars", "consent,transparency",
                 "--limit", "4", "--concurrency", "2", "--out", str(out), "--quiet"])
    assert code in (0, 1, 2)
    report = read_json(out)
    assert report["metadata"]["chatbot"] == {"adapter": "CallableAdapter", "callable": f"{bot_file}:repondre"}
    assert report["stats"]["totals"]["technical_errors"] == 0


def test_cli_audits_a_command_and_a_plugin_class(bot_file, tmp_path):
    for flags, name in ((["--adapter", "command", "--command", f'"{PY}" -c "import sys; print(sys.stdin.read())"'], "CommandAdapter"),
                        (["--adapter", "plugin", "--plugin", f"{bot_file}:MonBot", "--plugin-args", '{"prefixe": "Z"}'], "MonBot")):
        out = tmp_path / f"{name}.json"
        assert main(["audit", *flags, "--pillars", "consent", "--limit", "3", "--out", str(out), "--quiet"]) in (0, 1, 2)
        assert read_json(out)["metadata"]["chatbot"]["adapter"] == name


def test_cli_profiles(bot_file, tmp_path, capsys):
    config = tmp_path / "bots.json"
    config.write_text(json.dumps({"interne": {"adapter": "python", "callable": f"{bot_file}:repondre"}}), encoding="utf-8")
    assert main(["check-bot", "--chatbot-config", str(config), "--chatbot", "interne"]) == 0
    assert main(["audit", "--chatbot-config", str(config), "--chatbot", "interne", "--pillars", "consent", "--limit", "2",
                 "--out", str(tmp_path / "r.json"), "--quiet"]) in (0, 1, 2)
    assert read_json(tmp_path / "r.json")["metadata"]["chatbot"]["adapter"] == "CallableAdapter"
    capsys.readouterr()


@pytest.mark.parametrize("argv, message", [
    (["--adapter", "python"], "--callable"),
    (["--adapter", "command"], "--command"),
    (["--adapter", "plugin"], "--plugin"),
    (["--adapter", "python", "--callable", "absent.py:f"], "introuvable"),
    (["--chatbot", "x"], "--chatbot-config"),
    ([], "--adapter est requis"),
    (["--adapter", "demo-good", "--chatbot", "x", "--chatbot-config", "y.json"], "s'excluent"),
    (["--chatbot", "inconnu", "--chatbot-config", "__BOTS__"], "introuvable"),
])
def test_cli_configuration_errors(argv, message, bot_file, tmp_path, capsys):
    config = tmp_path / "bots.json"
    config.write_text(json.dumps({"interne": {"adapter": "python", "callable": f"{bot_file}:repondre"}}), encoding="utf-8")
    argv = [str(config) if a == "__BOTS__" else a for a in argv]
    assert main(["audit", "--quiet", *argv]) == 64
    assert message in capsys.readouterr().err


# --- encodage : un chatbot français ne doit jamais perdre ses accents ----------------------------------------------------

def test_accents_and_emoji_survive_a_round_trip_through_a_command():
    code = "import sys; print(sys.stdin.read())"
    text = "é à ç œ « ok » 🩺 — ñ"
    assert CommandAdapter([PY, "-c", code]).send([user(text)]).text == text


def test_a_program_that_writes_in_the_local_codepage_is_still_read_correctly():
    code = "import sys; sys.stdout.buffer.write('éàç œ'.encode('cp1252', 'replace'))"
    assert CommandAdapter([PY, "-c", code]).send([user("x")]).text.startswith("éàç")
