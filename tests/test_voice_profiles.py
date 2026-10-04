"""Voice profiles + vendor router (spec §7.7).

`resolve_profiles(env)` turns the environment into the three profiles (standard, halloween,
halloween_fallback) and applies the missing-key fallback: at LiveKit 1.8.3 the ElevenLabs plugin raises
ValueError at construction without ELEVEN_API_KEY, so a vendor is chosen only after its settings are seen.
`build_tts(profile)` imports the vendor plugin lazily and constructs its TTS without connecting.

Most tests install FAKE plugin modules, so no vendor code runs and nothing touches the network. The fakes
copy the real 1.8.3 constructor shapes (`VoiceSettings` requires `similarity_boost`), because a fake that is
more permissive than the plugin would pass a construction the real plugin rejects. The last tests build the
real plugins with dummy keys (construction opens no connection) to pin exactly that.
"""
import dataclasses
import logging
import subprocess
import sys
import types
from pathlib import Path
from typing import get_args

import livekit.plugins
import pytest
from livekit.agents import NOT_GIVEN
from livekit.agents.utils import is_given

from app.expressive import SPOOKY_TAGS
from app.voice_profiles import VoiceProfile, build_tts, resolve_profiles
from src.agent_prompt import HALLOWEEN_PERSONA

ELEVEN_ENV = {"ELEVEN_API_KEY": "k", "UG_HALLOWEEN_VOICE_ID": "v"}
OPENAI_ENV = {"UG_HALLOWEEN_TTS": "openai", "OPENAI_API_KEY": "k"}
EVERY_SETUP = [   # one of each way the Halloween voice can end up: each vendor, working or missing a setting
    pytest.param({}, id="nothing configured"),
    pytest.param(ELEVEN_ENV, id="elevenlabs"),
    pytest.param({"ELEVEN_API_KEY": "k"}, id="elevenlabs, no voice id"),
    pytest.param({**ELEVEN_ENV, **OPENAI_ENV}, id="openai"),
    pytest.param({"UG_HALLOWEEN_TTS": "openai"}, id="openai, no key"),
    pytest.param({"UG_HALLOWEEN_TTS": "deepgram"}, id="deepgram"),
]
_SETTINGS = ("UG_HALLOWEEN_TTS", "UG_HALLOWEEN_TTS_MODEL", "UG_HALLOWEEN_VOICE_ID", "UG_HALLOWEEN_STABILITY",
             "UG_HALLOWEEN_FALLBACK_VOICE", "ELEVEN_API_KEY", "OPENAI_API_KEY", "DEEPGRAM_API_KEY")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """build_tts reads the process environment: nothing from the developer's shell may leak in."""
    for name in _SETTINGS:
        monkeypatch.delenv(name, raising=False)


def configured(monkeypatch, env):
    """The profiles for `env`, with `env` also set as the process environment, where build_tts reads the
    model, voice and stability (as in production, where resolve_profiles is handed os.environ)."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return resolve_profiles(env)


# ------------------------------------------------------------------ fake plugins

class FakeTTS:
    """A vendor TTS stand-in: remembers how it was built and whether anything used it."""

    def __init__(self, vendor, **kwargs):
        self.vendor = vendor
        self.kwargs = kwargs
        self.used = []

    def prewarm(self):
        self.used.append("prewarm")

    def stream(self, **_):
        self.used.append("stream")

    def synthesize(self, *_, **__):
        self.used.append("synthesize")

    async def aclose(self):
        self.used.append("aclose")


@dataclasses.dataclass
class FakeVoiceSettings:
    """The real 1.8.3 dataclass: `stability` and `similarity_boost` have no default, the rest are unset."""
    stability: float
    similarity_boost: float
    style: float = NOT_GIVEN
    speed: float = NOT_GIVEN
    use_speaker_boost: bool = NOT_GIVEN


def given(settings):
    """The fields the plugin would actually use: it drops the unset ones (`_strip_nones`) before sending."""
    return {name: value for name, value in dataclasses.asdict(settings).items()
            if is_given(value) and value is not None}


def install_plugin(monkeypatch, name, **attrs):
    module = types.ModuleType(f"livekit.plugins.{name}")
    vars(module).update(attrs)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    # `from livekit.plugins import x` reads the package attribute before sys.modules, so a real plugin
    # imported by an earlier test would win over the fake
    monkeypatch.setattr(livekit.plugins, name, module, raising=False)


def block_plugin(monkeypatch, name):
    """Make `from livekit.plugins import <name>` fail, whether or not an earlier test imported the real one."""
    monkeypatch.setitem(sys.modules, f"livekit.plugins.{name}", None)
    monkeypatch.delattr(livekit.plugins, name, raising=False)


@pytest.fixture
def fake_plugins(monkeypatch):
    install_plugin(monkeypatch, "elevenlabs", VoiceSettings=FakeVoiceSettings,
                   TTS=lambda **kw: FakeTTS("elevenlabs", **kw))
    install_plugin(monkeypatch, "openai", TTS=lambda **kw: FakeTTS("openai", **kw))
    install_plugin(monkeypatch, "deepgram", TTS=lambda **kw: FakeTTS("deepgram", **kw))


# ------------------------------------------------------------------ resolve_profiles

def test_standard_profile_has_no_tags_no_persona():
    p = resolve_profiles({})["standard"]
    assert (p.key, p.vendor) == ("standard", "deepgram")
    assert p.tags == frozenset() and p.persona is None
    assert "aura-2-andromeda-en" in p.label


@pytest.mark.parametrize("env", EVERY_SETUP)
def test_three_profiles_each_keyed_by_its_own_key(env):
    """The controller swaps profiles by key, so `halloween` stays `halloween` even when it is Deepgram."""
    profiles = resolve_profiles(env)
    assert set(profiles) == {"standard", "halloween", "halloween_fallback"}
    assert all(p.key == key for key, p in profiles.items())


def test_elevenlabs_profile_when_the_key_is_present():
    p = resolve_profiles({"UG_HALLOWEEN_TTS": "elevenlabs", **ELEVEN_ENV})["halloween"]
    assert p.vendor == "elevenlabs"
    assert p.tags == frozenset(SPOOKY_TAGS)
    assert p.persona == HALLOWEEN_PERSONA
    assert "eleven_v3_conversational" in p.label


def test_elevenlabs_is_the_default_vendor():
    assert resolve_profiles(ELEVEN_ENV)["halloween"].vendor == "elevenlabs"


def test_elevenlabs_model_is_configurable_and_shown_in_the_label():
    p = resolve_profiles({**ELEVEN_ENV, "UG_HALLOWEEN_TTS_MODEL": "eleven_v3"})["halloween"]
    assert "eleven_v3" in p.label and "conversational" not in p.label


@pytest.mark.parametrize("model, performs_tags", [
    pytest.param("eleven_v3", True, id="v3"),
    pytest.param("eleven_v3_conversational", True, id="v3 conversational"),
    pytest.param("eleven_turbo_v2_5", False, id="turbo"),
    pytest.param("eleven_flash_v2_5", False, id="flash"),
    pytest.param("eleven_multilingual_v2", False, id="multilingual"),
    pytest.param("Eleven_V3", False, id="case-sensitive, like the plugin's own check"),
])
def test_only_elevenlabs_dialogue_models_get_tags(model, performs_tags):
    """Any other ElevenLabs model speaks "[laughs]" aloud (G13). Its profile keeps the spooky persona but gets no
    tags (fail closed): the tag filter then drops every cue, and the prompt never asks for one."""
    p = resolve_profiles({**ELEVEN_ENV, "UG_HALLOWEEN_TTS_MODEL": model})["halloween"]
    assert p.vendor == "elevenlabs"
    assert p.persona == HALLOWEEN_PERSONA
    assert p.tags == (frozenset(SPOOKY_TAGS) if performs_tags else frozenset())


def test_the_tag_gate_agrees_with_the_plugins_own_dialogue_routing():
    """`eleven_v3` is a copy of the plugin's rule for which models go to text-to-dialogue, the only path that
    performs tags. A stack bump that changes the rule or adds models must fail here, not quietly lose the tags
    or let a model speak them."""
    from livekit.plugins.elevenlabs.models import DIALOGUE_TTS_MODEL_PREFIX, TTSModels, is_dialogue_model

    from app.voice_profiles import DIALOGUE_MODEL_PREFIX

    assert DIALOGUE_MODEL_PREFIX == DIALOGUE_TTS_MODEL_PREFIX
    for model in get_args(TTSModels):
        tags = resolve_profiles({**ELEVEN_ENV, "UG_HALLOWEEN_TTS_MODEL": model})["halloween"].tags
        assert bool(tags) == is_dialogue_model(model), model


@pytest.mark.parametrize("env", [
    pytest.param({}, id="nothing configured"),
    pytest.param({"UG_HALLOWEEN_TTS": "elevenlabs"}, id="no key"),
    pytest.param({"UG_HALLOWEEN_TTS": "elevenlabs", "UG_HALLOWEEN_VOICE_ID": "v"}, id="no key, voice id set"),
    pytest.param({"ELEVEN_API_KEY": "  ", "UG_HALLOWEEN_VOICE_ID": "v"}, id="blank key"),
    pytest.param({"ELEVEN_API_KEY": "k"}, id="no voice id"),
    pytest.param({"ELEVEN_API_KEY": "k", "UG_HALLOWEEN_VOICE_ID": " "}, id="blank voice id"),
])
def test_elevenlabs_not_fully_configured_falls_back_to_deepgram_no_tags(env):
    p = resolve_profiles(env)["halloween"]
    assert p.key == "halloween" and p.vendor == "deepgram"
    assert p.tags == frozenset()
    assert p.persona == HALLOWEEN_PERSONA   # still spooky wording, in a darker Deepgram voice
    assert p.label == resolve_profiles(env)["halloween_fallback"].label


def test_the_fallback_is_logged_naming_what_is_missing_and_never_a_secret(caplog):
    with caplog.at_level(logging.WARNING, logger="app.voice_profiles"):
        resolve_profiles({"ELEVEN_API_KEY": "sk-secret"})   # the key is set; the voice id is not
    assert "UG_HALLOWEEN_VOICE_ID" in caplog.text
    assert "sk-secret" not in caplog.text


def test_openai_vendor_has_persona_but_no_inline_tags():
    p = resolve_profiles(OPENAI_ENV)["halloween"]
    assert p.vendor == "openai" and p.tags == frozenset()
    assert p.persona == HALLOWEEN_PERSONA
    assert "gpt-4o-mini-tts" in p.label


def test_openai_without_its_own_key_falls_back_to_deepgram():
    # ElevenLabs is fully configured here: the OpenAI choice must look at OPENAI_API_KEY, not at any key
    p = resolve_profiles({"UG_HALLOWEEN_TTS": "openai", **ELEVEN_ENV})["halloween"]
    assert p.vendor == "deepgram" and p.tags == frozenset() and p.persona == HALLOWEEN_PERSONA


def test_deepgram_can_be_chosen_outright():
    p = resolve_profiles({"UG_HALLOWEEN_TTS": "deepgram", **ELEVEN_ENV})["halloween"]
    assert p.vendor == "deepgram" and p.tags == frozenset() and p.persona == HALLOWEEN_PERSONA


def test_vendor_choice_ignores_case_and_padding():
    assert resolve_profiles({"UG_HALLOWEEN_TTS": " OpenAI ", "OPENAI_API_KEY": "k"})["halloween"].vendor == "openai"


def test_unknown_vendor_warns_and_uses_the_default_instead_of_crashing(caplog):
    with caplog.at_level(logging.WARNING, logger="app.voice_profiles"):
        p = resolve_profiles({"UG_HALLOWEEN_TTS": "azure", **ELEVEN_ENV})["halloween"]
    assert p.vendor == "elevenlabs"
    assert "azure" in caplog.text


def test_halloween_fallback_is_deepgram_with_persona_and_no_tags_even_when_elevenlabs_works():
    p = resolve_profiles(ELEVEN_ENV)["halloween_fallback"]
    assert (p.vendor, p.tags, p.persona) == ("deepgram", frozenset(), HALLOWEEN_PERSONA)


def test_fallback_voice_defaults_to_zeus_and_can_be_overridden():
    assert "aura-2-zeus-en" in resolve_profiles({})["halloween_fallback"].label
    custom = resolve_profiles({"UG_HALLOWEEN_FALLBACK_VOICE": "aura-2-orion-en"})
    assert "aura-2-orion-en" in custom["halloween_fallback"].label
    assert "aura-2-andromeda-en" in custom["standard"].label   # the normal voice is not the fallback's business


@pytest.mark.parametrize("env", EVERY_SETUP)
def test_tags_belong_to_the_elevenlabs_profile_alone(env):
    """Any other voice reads a `[tag]` aloud (G13); an empty set makes the tag filter drop every cue."""
    for p in resolve_profiles(env).values():
        assert bool(p.tags) == (p.vendor == "elevenlabs"), p


# ------------------------------------------------------------------ build_tts, with fake plugins

@pytest.mark.usefixtures("fake_plugins")
def test_elevenlabs_tts_is_built_with_model_voice_and_stability_only(monkeypatch):
    env = {**ELEVEN_ENV, "UG_HALLOWEEN_TTS_MODEL": "eleven_v3", "UG_HALLOWEEN_VOICE_ID": "voice-123",
           "UG_HALLOWEEN_STABILITY": "0.8"}
    tts = build_tts(configured(monkeypatch, env)["halloween"])
    assert tts.vendor == "elevenlabs"
    assert set(tts.kwargs) == {"model", "voice_id", "voice_settings"}
    assert tts.kwargs["model"] == "eleven_v3"
    assert tts.kwargs["voice_id"] == "voice-123"
    # a dialogue model honors stability alone: nothing else may be set, or the plugin warns on every call
    assert given(tts.kwargs["voice_settings"]) == {"stability": 0.8}


@pytest.mark.usefixtures("fake_plugins")
def test_elevenlabs_defaults_to_the_dialogue_model_at_half_stability(monkeypatch):
    tts = build_tts(configured(monkeypatch, ELEVEN_ENV)["halloween"])
    assert tts.kwargs["model"] == "eleven_v3_conversational"
    assert given(tts.kwargs["voice_settings"]) == {"stability": 0.5}


@pytest.mark.usefixtures("fake_plugins")
@pytest.mark.parametrize("raw, expected", [("0", 0.0), ("0.3", 0.3), ("1", 1.0)])
def test_stability_takes_any_value_in_range_including_zero(monkeypatch, raw, expected):
    tts = build_tts(configured(monkeypatch, {**ELEVEN_ENV, "UG_HALLOWEEN_STABILITY": raw})["halloween"])
    assert given(tts.kwargs["voice_settings"]) == {"stability": expected}   # 0.0 is a real setting, not "unset"


@pytest.mark.usefixtures("fake_plugins")
@pytest.mark.parametrize("raw", ["loud", "1.5", "-0.1", "nan", "inf"])
def test_unusable_stability_is_replaced_by_the_default_with_a_warning(monkeypatch, caplog, raw):
    profile = configured(monkeypatch, {**ELEVEN_ENV, "UG_HALLOWEEN_STABILITY": raw})["halloween"]
    with caplog.at_level(logging.WARNING, logger="app.voice_profiles"):
        tts = build_tts(profile)
    assert given(tts.kwargs["voice_settings"]) == {"stability": 0.5}
    assert "UG_HALLOWEEN_STABILITY" in caplog.text


@pytest.mark.usefixtures("fake_plugins")
def test_an_elevenlabs_tts_without_a_voice_id_is_refused_rather_than_given_the_plugins_default_voice():
    # resolve_profiles never yields an ElevenLabs profile without one; a hand-built one must not speak
    profile = VoiceProfile("halloween", "ElevenLabs", "elevenlabs", frozenset(SPOOKY_TAGS), HALLOWEEN_PERSONA)
    with pytest.raises(ValueError, match="UG_HALLOWEEN_VOICE_ID"):
        build_tts(profile)


@pytest.mark.usefixtures("fake_plugins")
def test_openai_tts_gets_the_mini_tts_model_and_a_spooky_style(monkeypatch):
    tts = build_tts(configured(monkeypatch, OPENAI_ENV)["halloween"])
    assert tts.vendor == "openai"
    assert set(tts.kwargs) == {"model", "instructions"}
    assert tts.kwargs["model"] == "gpt-4o-mini-tts"
    assert "spooky" in tts.kwargs["instructions"].lower()


@pytest.mark.usefixtures("fake_plugins")
@pytest.mark.parametrize("env, expected_voice", [
    pytest.param({}, "aura-2-zeus-en", id="default"),
    pytest.param({"UG_HALLOWEEN_FALLBACK_VOICE": "aura-2-orion-en"}, "aura-2-orion-en", id="override"),
])
@pytest.mark.parametrize("key", ["halloween_fallback", "halloween"])   # with no ElevenLabs key, halloween IS Deepgram
def test_deepgram_fallback_speaks_in_the_fallback_voice(monkeypatch, env, expected_voice, key):
    tts = build_tts(configured(monkeypatch, env)[key])
    assert tts.vendor == "deepgram"
    assert tts.kwargs == {"model": expected_voice}


@pytest.mark.usefixtures("fake_plugins")
def test_standard_profile_builds_the_sessions_own_voice_not_the_fallback(monkeypatch):
    tts = build_tts(configured(monkeypatch, {"UG_HALLOWEEN_FALLBACK_VOICE": "aura-2-orion-en"})["standard"])
    assert tts.vendor == "deepgram"
    assert tts.kwargs == {"model": "aura-2-andromeda-en"}


@pytest.mark.usefixtures("fake_plugins")
@pytest.mark.parametrize("env, key", [
    pytest.param(ELEVEN_ENV, "halloween", id="elevenlabs"),
    pytest.param(OPENAI_ENV, "halloween", id="openai"),
    pytest.param({}, "halloween_fallback", id="deepgram fallback"),
    pytest.param({}, "standard", id="standard"),
])
def test_building_a_tts_connects_to_nothing(monkeypatch, env, key):
    assert build_tts(configured(monkeypatch, env)[key]).used == []   # no prewarm, stream, synthesize


@pytest.mark.usefixtures("fake_plugins")
@pytest.mark.parametrize("env, vendor", [
    pytest.param(ELEVEN_ENV, "elevenlabs", id="elevenlabs"),
    pytest.param(OPENAI_ENV, "openai", id="openai"),
    pytest.param({}, "deepgram", id="deepgram"),
])
def test_only_the_profiles_own_plugin_is_imported(monkeypatch, env, vendor):
    for other in {"elevenlabs", "openai", "deepgram"} - {vendor}:
        block_plugin(monkeypatch, other)
    assert build_tts(configured(monkeypatch, env)["halloween"]).vendor == vendor


def test_an_unknown_vendor_is_refused():
    with pytest.raises(ValueError, match="azure"):
        build_tts(VoiceProfile("halloween", "Azure", "azure", frozenset(), None))


# ------------------------------------------------------------------ the module itself

def test_module_imports_without_livekit():
    """Spec §7.1: no LiveKit at import. The plugins are imported inside build_tts, when a voice is built."""
    code = (
        "import sys, app.voice_profiles; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'livekit'); "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=Path(__file__).resolve().parents[1])


# ------------------------------------------------------------------ build_tts, with the real plugins

def test_the_real_elevenlabs_plugin_accepts_the_construction_and_has_nothing_to_warn_about(monkeypatch, caplog):
    """`VoiceSettings(stability=...)` alone is a TypeError (similarity_boost is required), and setting
    similarity_boost makes the plugin warn, on every call, that a dialogue model ignores it. Only the plugin
    can say that what we pass is accepted and quiet; a dummy key is enough, as nothing connects."""
    profile = configured(monkeypatch, ELEVEN_ENV)["halloween"]
    with caplog.at_level(logging.WARNING):
        tts = build_tts(profile)
    assert (tts.provider, tts.model) == ("ElevenLabs", "eleven_v3_conversational")
    assert [r.getMessage() for r in caplog.records if r.name.startswith("livekit")] == []


def test_the_real_openai_and_deepgram_plugins_accept_the_construction(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    profiles = configured(monkeypatch, OPENAI_ENV)
    assert build_tts(profiles["halloween"]).model == "gpt-4o-mini-tts"
    assert build_tts(profiles["halloween_fallback"]).model == "aura-2-zeus-en"
    assert build_tts(profiles["standard"]).model == "aura-2-andromeda-en"
