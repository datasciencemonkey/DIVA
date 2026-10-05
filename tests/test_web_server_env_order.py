from pathlib import Path

WEB_SERVER = Path(__file__).resolve().parent.parent / "app" / "web_server.py"


def _line_of(prefix: str) -> int:
    for n, line in enumerate(WEB_SERVER.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith(prefix):
            return n
    raise AssertionError(f"no line starts with {prefix!r} in {WEB_SERVER}")


def test_env_local_is_loaded_before_the_src_imports():
    """Issue #41: db.py reads UG_SCHEMA and embeddings.py reads UG_EMBED_MODEL / UG_EMBED_DIM at import
    time, so if app/web_server.py imports src before calling _load_env_local(), values that live only in
    .env.local are ignored by the web tier (app/agent.py already loads it first)."""
    assert _line_of("_load_env_local()") < _line_of("from src.")
