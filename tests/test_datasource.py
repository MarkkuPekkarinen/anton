from __future__ import annotations

import json
import os
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anton.data_vault import DataVault
from anton.datasource_registry import (
    AuthMethod,
    DatasourceEngine,
    DatasourceField,
    DatasourceRegistry,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def vault_dir(tmp_path):
    return tmp_path / "data_vault"


@pytest.fixture()
def vault(vault_dir):
    return DataVault(vault_dir=vault_dir)


@pytest.fixture()
def datasources_md(tmp_path):
    """Write a minimal datasources.md and return its path."""
    path = tmp_path / "datasources.md"
    path.write_text(dedent("""\
        ## PostgreSQL

        ```yaml
        engine: postgresql
        display_name: PostgreSQL
        pip: psycopg2-binary
        name_from: database
        fields:
          - name: host
            required: true
            description: hostname or IP
          - name: port
            required: true
            default: "5432"
            description: port number
          - name: database
            required: true
            description: database name
          - name: user
            required: true
            description: username
          - name: password
            required: true
            secret: true
            description: password
          - name: schema
            required: false
            description: defaults to public
        test_snippet: |
          import psycopg2
          conn = psycopg2.connect(
              host=os.environ["DS_HOST"],
              port=os.environ["DS_PORT"],
              dbname=os.environ["DS_DATABASE"],
              user=os.environ["DS_USER"],
              password=os.environ["DS_PASSWORD"],
          )
          conn.close()
          print("ok")
        ```

        ## HubSpot

        ```yaml
        engine: hubspot
        display_name: HubSpot
        pip: hubspot-api-client
        name_from: access_token
        auth_method: choice
        auth_methods:
          - name: private_app
            display: Private App token (recommended)
            fields:
              - name: access_token
                required: true
                secret: true
                description: pat-na1-xxx token
          - name: oauth
            display: OAuth 2.0
            fields:
              - name: client_id
                required: true
                description: OAuth client ID
              - name: client_secret
                required: true
                secret: true
                description: OAuth client secret
        test_snippet: |
          print("ok")
        ```
    """))
    return path


@pytest.fixture()
def registry(datasources_md, tmp_path):
    """Registry pointing at our temp datasources.md, no user overrides."""
    reg = DatasourceRegistry.__new__(DatasourceRegistry)
    reg._engines = {}
    from anton.datasource_registry import _parse_file
    reg._engines = _parse_file(datasources_md)
    return reg


# ─────────────────────────────────────────────────────────────────────────────
# DataVault — save / load / delete
# ─────────────────────────────────────────────────────────────────────────────


class TestDataVaultSaveLoad:
    def test_save_creates_file(self, vault, vault_dir):
        vault.save("postgresql", "prod_db", {"host": "db.example.com", "port": "5432"})
        assert (vault_dir / "postgresql-prod_db").is_file()

    def test_save_file_permissions(self, vault, vault_dir):
        vault.save("postgresql", "prod_db", {"host": "db.example.com"})
        path = vault_dir / "postgresql-prod_db"
        mode = oct(path.stat().st_mode)[-3:]
        assert mode == "600"

    def test_vault_dir_permissions(self, vault, vault_dir):
        vault.save("postgresql", "prod_db", {"host": "db.example.com"})
        mode = oct(vault_dir.stat().st_mode)[-3:]
        assert mode == "700"

    def test_load_returns_fields(self, vault):
        creds = {"host": "db.example.com", "port": "5432", "password": "secret"}
        vault.save("postgresql", "prod_db", creds)
        loaded = vault.load("postgresql", "prod_db")
        assert loaded == creds

    def test_load_missing_returns_none(self, vault):
        assert vault.load("postgresql", "nonexistent") is None

    def test_load_corrupt_file_returns_none(self, vault, vault_dir):
        vault._ensure_dir()
        (vault_dir / "postgresql-bad").write_text("not json")
        assert vault.load("postgresql", "bad") is None

    def test_save_overwrites_existing(self, vault):
        vault.save("postgresql", "prod_db", {"host": "old.host"})
        vault.save("postgresql", "prod_db", {"host": "new.host"})
        assert vault.load("postgresql", "prod_db") == {"host": "new.host"}

    def test_delete_existing(self, vault, vault_dir):
        vault.save("postgresql", "prod_db", {"host": "x"})
        result = vault.delete("postgresql", "prod_db")
        assert result is True
        assert not (vault_dir / "postgresql-prod_db").is_file()

    def test_delete_missing_returns_false(self, vault):
        assert vault.delete("postgresql", "ghost") is False

    def test_special_chars_sanitized_in_filename(self, vault, vault_dir):
        vault.save("postgresql", "my db/prod", {"host": "x"})
        files = list(vault_dir.iterdir())
        assert len(files) == 1
        assert "/" not in files[0].name

    def test_json_contains_metadata(self, vault, vault_dir):
        vault.save("postgresql", "prod_db", {"host": "x"})
        raw = json.loads((vault_dir / "postgresql-prod_db").read_text())
        assert raw["engine"] == "postgresql"
        assert raw["name"] == "prod_db"
        assert "created_at" in raw
        assert raw["fields"] == {"host": "x"}


# ─────────────────────────────────────────────────────────────────────────────
# DataVault — list_connections
# ─────────────────────────────────────────────────────────────────────────────


class TestDataVaultListConnections:
    def test_empty_vault(self, vault):
        assert vault.list_connections() == []

    def test_lists_all_connections(self, vault):
        vault.save("postgresql", "prod_db", {"host": "a"})
        vault.save("hubspot", "main", {"access_token": "pat-xxx"})
        conns = vault.list_connections()
        engines = {c["engine"] for c in conns}
        assert engines == {"postgresql", "hubspot"}

    def test_skips_corrupt_files(self, vault, vault_dir):
        vault._ensure_dir()
        vault.save("postgresql", "good", {"host": "x"})
        (vault_dir / "postgresql-bad").write_text("{{not json")
        conns = vault.list_connections()
        assert len(conns) == 1
        assert conns[0]["name"] == "good"

    def test_vault_dir_missing_returns_empty(self, vault):
        # vault_dir was never created
        assert vault.list_connections() == []


# ─────────────────────────────────────────────────────────────────────────────
# DataVault — inject_env / clear_ds_env
# ─────────────────────────────────────────────────────────────────────────────


class TestDataVaultEnvInjection:
    def test_inject_sets_ds_vars(self, vault):
        vault.save("postgresql", "prod_db", {"host": "db.example.com", "password": "s3cr3t"})
        var_names = vault.inject_env("postgresql", "prod_db")
        assert os.environ.get("DS_HOST") == "db.example.com"
        assert os.environ.get("DS_PASSWORD") == "s3cr3t"
        assert set(var_names) == {"DS_HOST", "DS_PASSWORD"}
        # Cleanup
        vault.clear_ds_env()

    def test_inject_missing_returns_none(self, vault):
        result = vault.inject_env("postgresql", "ghost")
        assert result is None

    def test_clear_removes_ds_vars(self, vault):
        vault.save("postgresql", "prod_db", {"host": "x"})
        vault.inject_env("postgresql", "prod_db")
        vault.clear_ds_env()
        assert "DS_HOST" not in os.environ

    def test_clear_leaves_non_ds_vars(self, vault):
        os.environ["MY_VAR"] = "untouched"
        vault.clear_ds_env()
        assert os.environ.get("MY_VAR") == "untouched"
        del os.environ["MY_VAR"]

    def test_inject_uppercases_field_names(self, vault):
        vault.save("postgresql", "prod_db", {"access_token": "tok123"})
        vault.inject_env("postgresql", "prod_db")
        assert os.environ.get("DS_ACCESS_TOKEN") == "tok123"
        vault.clear_ds_env()


# ─────────────────────────────────────────────────────────────────────────────
# DataVault — next_connection_number
# ─────────────────────────────────────────────────────────────────────────────


class TestDataVaultNextConnectionNumber:
    def test_returns_one_when_empty(self, vault):
        assert vault.next_connection_number("postgresql") == 1

    def test_increments_past_existing(self, vault):
        vault.save("postgresql", "1", {"host": "a"})
        vault.save("postgresql", "2", {"host": "b"})
        assert vault.next_connection_number("postgresql") == 3

    def test_ignores_named_connections(self, vault):
        # "prod_db" is not a digit — should not affect numbering
        vault.save("postgresql", "prod_db", {"host": "a"})
        assert vault.next_connection_number("postgresql") == 1

    def test_does_not_confuse_engines(self, vault):
        vault.save("hubspot", "1", {"access_token": "x"})
        vault.save("hubspot", "2", {"access_token": "y"})
        # postgresql counter is independent
        assert vault.next_connection_number("postgresql") == 1


# ─────────────────────────────────────────────────────────────────────────────
# DatasourceRegistry — lookup
# ─────────────────────────────────────────────────────────────────────────────


class TestDatasourceRegistry:
    def test_get_by_slug(self, registry):
        engine = registry.get("postgresql")
        assert engine is not None
        assert engine.display_name == "PostgreSQL"

    def test_get_missing_returns_none(self, registry):
        assert registry.get("mysql") is None

    def test_find_by_name_exact(self, registry):
        assert registry.find_by_name("PostgreSQL") is not None

    def test_find_by_name_case_insensitive(self, registry):
        assert registry.find_by_name("postgresql") is not None
        assert registry.find_by_name("POSTGRESQL") is not None

    def test_find_by_slug(self, registry):
        # engine slug is also accepted
        assert registry.find_by_name("postgresql") is not None

    def test_find_unknown_returns_none(self, registry):
        assert registry.find_by_name("MySQL") is None

    def test_all_engines_sorted(self, registry):
        engines = registry.all_engines()
        names = [e.display_name for e in engines]
        assert names == sorted(names)

    def test_fields_parsed_correctly(self, registry):
        engine = registry.get("postgresql")
        field_names = [f.name for f in engine.fields]
        assert "host" in field_names
        assert "password" in field_names

    def test_secret_flag_on_password(self, registry):
        engine = registry.get("postgresql")
        pw = next(f for f in engine.fields if f.name == "password")
        assert pw.secret is True

    def test_required_flag(self, registry):
        engine = registry.get("postgresql")
        schema = next(f for f in engine.fields if f.name == "schema")
        assert schema.required is False

    def test_default_value_on_port(self, registry):
        engine = registry.get("postgresql")
        port = next(f for f in engine.fields if f.name == "port")
        assert port.default == "5432"

    def test_pip_field(self, registry):
        engine = registry.get("postgresql")
        assert engine.pip == "psycopg2-binary"

    def test_test_snippet_present(self, registry):
        engine = registry.get("postgresql")
        assert "print(\"ok\")" in engine.test_snippet

    def test_auth_method_choice_parsed(self, registry):
        engine = registry.get("hubspot")
        assert engine.auth_method == "choice"
        assert len(engine.auth_methods) == 2
        method_names = [m.name for m in engine.auth_methods]
        assert "private_app" in method_names
        assert "oauth" in method_names

    def test_auth_method_fields_parsed(self, registry):
        engine = registry.get("hubspot")
        private = next(m for m in engine.auth_methods if m.name == "private_app")
        assert len(private.fields) == 1
        assert private.fields[0].name == "access_token"
        assert private.fields[0].secret is True


# ─────────────────────────────────────────────────────────────────────────────
# DatasourceRegistry — derive_name
# ─────────────────────────────────────────────────────────────────────────────


class TestDeriveConnectionName:
    def test_single_field_name_from(self, registry):
        engine = registry.get("postgresql")  # name_from: database
        name = registry.derive_name(engine, {"database": "prod_db", "host": "x"})
        assert name == "prod_db"

    def test_missing_name_from_field_returns_empty(self, registry):
        engine = registry.get("postgresql")
        name = registry.derive_name(engine, {"host": "x"})  # no "database"
        assert name == ""

    def test_no_name_from_returns_empty(self):
        engine = DatasourceEngine(engine="test", display_name="Test", name_from="")
        reg = DatasourceRegistry.__new__(DatasourceRegistry)
        reg._engines = {}
        name = reg.derive_name(engine, {"host": "x"})
        assert name == ""

    def test_list_name_from(self):
        engine = DatasourceEngine(
            engine="test",
            display_name="Test",
            name_from=["host", "database"],
        )
        reg = DatasourceRegistry.__new__(DatasourceRegistry)
        reg._engines = {}
        name = reg.derive_name(engine, {"host": "db.example.com", "database": "prod"})
        assert name == "db.example.com_prod"

    def test_list_name_from_skips_missing(self):
        engine = DatasourceEngine(
            engine="test",
            display_name="Test",
            name_from=["host", "database"],
        )
        reg = DatasourceRegistry.__new__(DatasourceRegistry)
        reg._engines = {}
        name = reg.derive_name(engine, {"host": "db.example.com"})
        assert name == "db.example.com"


# ─────────────────────────────────────────────────────────────────────────────
# DatasourceRegistry — user overrides
# ─────────────────────────────────────────────────────────────────────────────


class TestDatasourceRegistryUserOverrides:
    def test_user_override_wins(self, tmp_path, datasources_md):
        """A user-defined engine with same slug overrides the builtin."""
        user_md = tmp_path / "user_datasources.md"
        user_md.write_text(dedent("""\
            ## PostgreSQL

            ```yaml
            engine: postgresql
            display_name: PostgreSQL (custom)
            pip: psycopg2
            fields:
              - name: host
                required: true
                description: custom host field
            test_snippet: print("ok")
            ```
        """))

        from anton.datasource_registry import _parse_file

        builtin = _parse_file(datasources_md)
        user = _parse_file(user_md)
        merged = {**builtin, **user}

        assert merged["postgresql"].display_name == "PostgreSQL (custom)"
        assert merged["postgresql"].pip == "psycopg2"

    def test_missing_user_file_falls_back_to_builtin(self, tmp_path, datasources_md):
        from anton.datasource_registry import _parse_file

        user_engines = _parse_file(tmp_path / "nonexistent.md")
        assert user_engines == {}


# ─────────────────────────────────────────────────────────────────────────────
# _handle_connect_datasource — integration-style (mocked I/O)
# ─────────────────────────────────────────────────────────────────────────────


class TestHandleConnectDatasource:
    """Test the slash-command handler with mocked prompts and scratchpad."""

    def _make_session(self):
        from anton.chat import ChatSession

        mock_llm = AsyncMock()
        session = ChatSession(mock_llm)
        session._scratchpads = AsyncMock()
        return session

    def _make_cell(self, stdout="ok", stderr="", error=None):
        cell = MagicMock()
        cell.stdout = stdout
        cell.stderr = stderr
        cell.error = error
        return cell

    @pytest.mark.asyncio
    async def test_unknown_engine_returns_early(self, registry, vault_dir, capsys):
        """Typing an unknown engine name aborts without saving anything."""
        from anton.chat import _handle_connect_datasource

        session = self._make_session()
        console = MagicMock()
        console.print = MagicMock()

        with (
            patch("anton.chat.DataVault", return_value=DataVault(vault_dir=vault_dir)),
            patch("anton.chat.DatasourceRegistry", return_value=registry),
            patch("rich.prompt.Prompt.ask", return_value="MySQL"),
        ):
            result = await _handle_connect_datasource(console, session._scratchpads, session)

        assert result is session  # unchanged session
        assert DataVault(vault_dir=vault_dir).list_connections() == []

    @pytest.mark.asyncio
    async def test_partial_save_on_n_answer(self, registry, vault_dir):
        """Answering 'n' saves partial credentials and returns without testing."""
        from anton.chat import _handle_connect_datasource

        session = self._make_session()
        console = MagicMock()
        console.print = MagicMock()

        vault = DataVault(vault_dir=vault_dir)
        prompt_responses = iter(["PostgreSQL", "n", "db.example.com", "", "", "", "", ""])

        with (
            patch("anton.chat.DataVault", return_value=vault),
            patch("anton.chat.DatasourceRegistry", return_value=registry),
            patch("rich.prompt.Prompt.ask", side_effect=lambda *a, **kw: next(prompt_responses)),
        ):
            result = await _handle_connect_datasource(console, session._scratchpads, session)

        conns = vault.list_connections()
        assert len(conns) == 1
        assert conns[0]["engine"] == "postgresql"
        # Partial connections get auto-numbered names
        assert conns[0]["name"].isdigit()
        # Scratchpad was NOT used for testing
        session._scratchpads.get_or_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_connection_saves_and_injects_history(self, registry, vault_dir):
        """Happy path: test passes, credentials saved, history entry added."""
        from anton.chat import _handle_connect_datasource

        session = self._make_session()
        console = MagicMock()
        console.print = MagicMock()
        vault = DataVault(vault_dir=vault_dir)

        pad = AsyncMock()
        pad.execute = AsyncMock(return_value=self._make_cell(stdout="ok"))
        session._scratchpads.get_or_create = AsyncMock(return_value=pad)

        prompt_responses = iter([
            "PostgreSQL",       # engine choice
            "y",                # have all credentials
            "db.example.com",   # host
            "5432",             # port
            "prod_db",          # database
            "alice",            # user
            "s3cr3t",           # password
            "",                 # schema (optional)
        ])

        with (
            patch("anton.chat.DataVault", return_value=vault),
            patch("anton.chat.DatasourceRegistry", return_value=registry),
            patch("rich.prompt.Prompt.ask", side_effect=lambda *a, **kw: next(prompt_responses)),
        ):
            result = await _handle_connect_datasource(console, session._scratchpads, session)

        # Credentials saved
        conns = vault.list_connections()
        assert len(conns) == 1
        saved = vault.load("postgresql", conns[0]["name"])
        assert saved["host"] == "db.example.com"
        assert saved["password"] == "s3cr3t"

        # History entry injected
        assert result._history
        last = result._history[-1]
        assert last["role"] == "assistant"
        assert "postgresql" in last["content"].lower()

    @pytest.mark.asyncio
    async def test_failed_test_offers_retry(self, registry, vault_dir):
        """Connection test failure prompts for retry; success on second attempt saves."""
        from anton.chat import _handle_connect_datasource

        session = self._make_session()
        console = MagicMock()
        console.print = MagicMock()
        vault = DataVault(vault_dir=vault_dir)

        fail_cell = self._make_cell(stdout="", stderr="password authentication failed")
        ok_cell = self._make_cell(stdout="ok")
        pad = AsyncMock()
        pad.execute = AsyncMock(side_effect=[fail_cell, ok_cell])
        session._scratchpads.get_or_create = AsyncMock(return_value=pad)

        prompt_responses = iter([
            "PostgreSQL",       # engine
            "y",                # have all creds
            "db.example.com",   # host
            "5432",             # port
            "prod_db",          # database
            "alice",            # user
            "wrongpassword",    # password (first attempt - fails)
            "",                 # schema
            "y",                # retry?
            "correctpassword",  # new password
        ])

        with (
            patch("anton.chat.DataVault", return_value=vault),
            patch("anton.chat.DatasourceRegistry", return_value=registry),
            patch("rich.prompt.Prompt.ask", side_effect=lambda *a, **kw: next(prompt_responses)),
        ):
            result = await _handle_connect_datasource(console, session._scratchpads, session)

        # Should have saved after second attempt
        conns = vault.list_connections()
        assert len(conns) == 1
        saved = vault.load("postgresql", conns[0]["name"])
        assert saved["password"] == "correctpassword"

    @pytest.mark.asyncio
    async def test_failed_test_no_retry_returns_without_saving(self, registry, vault_dir):
        """Declining retry on failed test leaves vault empty."""
        from anton.chat import _handle_connect_datasource

        session = self._make_session()
        console = MagicMock()
        console.print = MagicMock()
        vault = DataVault(vault_dir=vault_dir)

        fail_cell = self._make_cell(stdout="", error="connection refused")
        pad = AsyncMock()
        pad.execute = AsyncMock(return_value=fail_cell)
        session._scratchpads.get_or_create = AsyncMock(return_value=pad)

        prompt_responses = iter([
            "PostgreSQL", "y",
            "db.example.com", "5432", "prod_db", "alice", "badpass", "",
            "n",  # don't retry
        ])

        with (
            patch("anton.chat.DataVault", return_value=vault),
            patch("anton.chat.DatasourceRegistry", return_value=registry),
            patch("rich.prompt.Prompt.ask", side_effect=lambda *a, **kw: next(prompt_responses)),
        ):
            result = await _handle_connect_datasource(console, session._scratchpads, session)

        assert vault.list_connections() == []
        # No history injection since save never happened
        assert not result._history

    @pytest.mark.asyncio
    async def test_ds_env_cleaned_up_after_test(self, registry, vault_dir):
        """DS_* env vars must not leak into os.environ after connection testing."""
        from anton.chat import _handle_connect_datasource

        session = self._make_session()
        console = MagicMock()
        console.print = MagicMock()
        vault = DataVault(vault_dir=vault_dir)

        pad = AsyncMock()
        pad.execute = AsyncMock(return_value=self._make_cell(stdout="ok"))
        session._scratchpads.get_or_create = AsyncMock(return_value=pad)

        prompt_responses = iter([
            "PostgreSQL", "y",
            "db.example.com", "5432", "prod_db", "alice", "s3cr3t", "",
        ])

        with (
            patch("anton.chat.DataVault", return_value=vault),
            patch("anton.chat.DatasourceRegistry", return_value=registry),
            patch("rich.prompt.Prompt.ask", side_effect=lambda *a, **kw: next(prompt_responses)),
        ):
            await _handle_connect_datasource(console, session._scratchpads, session)

        # No DS_* variables should remain
        leaked = [k for k in os.environ if k.startswith("DS_")]
        assert leaked == [], f"DS_* vars leaked: {leaked}"

    @pytest.mark.asyncio
    async def test_auth_method_choice_selects_fields(self, registry, vault_dir):
        """Selecting an auth method filters to that method's fields only."""
        from anton.chat import _handle_connect_datasource

        session = self._make_session()
        console = MagicMock()
        console.print = MagicMock()
        vault = DataVault(vault_dir=vault_dir)

        pad = AsyncMock()
        pad.execute = AsyncMock(return_value=self._make_cell(stdout="ok"))
        session._scratchpads.get_or_create = AsyncMock(return_value=pad)

        prompt_responses = iter([
            "HubSpot",          # engine
            "1",                # auth method: private_app
            "y",                # have all creds
            "pat-na1-abc123",   # access_token
        ])

        with (
            patch("anton.chat.DataVault", return_value=vault),
            patch("anton.chat.DatasourceRegistry", return_value=registry),
            patch("rich.prompt.Prompt.ask", side_effect=lambda *a, **kw: next(prompt_responses)),
        ):
            await _handle_connect_datasource(console, session._scratchpads, session)

        conns = vault.list_connections()
        assert len(conns) == 1
        saved = vault.load("hubspot", conns[0]["name"])
        # Only private_app fields collected — no client_id or client_secret
        assert "access_token" in saved
        assert "client_id" not in saved
        assert "client_secret" not in saved

    @pytest.mark.asyncio
    async def test_selective_field_collection(self, registry, vault_dir):
        """Typing 'host,user,password' collects only those three fields."""
        from anton.chat import _handle_connect_datasource

        session = self._make_session()
        console = MagicMock()
        console.print = MagicMock()
        vault = DataVault(vault_dir=vault_dir)

        pad = AsyncMock()
        pad.execute = AsyncMock(return_value=self._make_cell(stdout="ok"))
        session._scratchpads.get_or_create = AsyncMock(return_value=pad)

        prompt_responses = iter([
            "PostgreSQL",           # engine
            "host,user,password",   # selective list
            "db.example.com",       # host
            "alice",                # user
            "s3cr3t",               # password
        ])

        with (
            patch("anton.chat.DataVault", return_value=vault),
            patch("anton.chat.DatasourceRegistry", return_value=registry),
            patch("rich.prompt.Prompt.ask", side_effect=lambda *a, **kw: next(prompt_responses)),
        ):
            await _handle_connect_datasource(console, session._scratchpads, session)

        conns = vault.list_connections()
        assert len(conns) == 1
        saved = vault.load("postgresql", conns[0]["name"])
        assert set(saved.keys()) == {"host", "user", "password"}


# ─────────────────────────────────────────────────────────────────────────────
# Credential scrubbing
# ─────────────────────────────────────────────────────────────────────────────


class TestCredentialScrubbing:
    """_scrub_credentials and _register_secret_vars."""

    def setup_method(self):
        # Reset the module-level sets before each test
        from anton.chat import _DS_KNOWN_VARS, _DS_SECRET_VARS
        _DS_SECRET_VARS.clear()
        _DS_KNOWN_VARS.clear()

    def test_register_secret_vars_adds_secret_fields(self, registry):
        """Secret fields are added to _DS_SECRET_VARS; non-secret fields are not."""
        from anton.chat import _DS_SECRET_VARS, _register_secret_vars

        pg = registry.get("postgresql")
        assert pg is not None
        _register_secret_vars(pg)

        assert "DS_PASSWORD" in _DS_SECRET_VARS
        # host and port are not secret in the fixture definition
        assert "DS_HOST" not in _DS_SECRET_VARS
        assert "DS_PORT" not in _DS_SECRET_VARS

    def test_scrub_replaces_registered_secret_value(self):
        """A registered secret value is replaced with its placeholder."""
        import os
        from anton.chat import _DS_SECRET_VARS, _scrub_credentials

        _DS_SECRET_VARS.add("DS_ACCESS_TOKEN")
        os.environ["DS_ACCESS_TOKEN"] = "supersecrettoken123"
        try:
            result = _scrub_credentials("token is supersecrettoken123 here")
            assert "supersecrettoken123" not in result
            assert "[DS_ACCESS_TOKEN]" in result
        finally:
            del os.environ["DS_ACCESS_TOKEN"]
            _DS_SECRET_VARS.discard("DS_ACCESS_TOKEN")

    def test_scrub_leaves_non_secret_field_readable(self, registry):
        """Non-secret DS_* values (host, port) are left untouched."""
        import os
        from anton.chat import _register_secret_vars, _scrub_credentials

        pg = registry.get("postgresql")
        assert pg is not None
        _register_secret_vars(pg)

        os.environ["DS_HOST"] = "db.example.com"
        os.environ["DS_PASSWORD"] = "s3cr3tpassword99"
        try:
            result = _scrub_credentials("host=db.example.com pass=s3cr3tpassword99")
            assert "db.example.com" in result          # host left readable
            assert "s3cr3tpassword99" not in result    # password redacted
            assert "[DS_PASSWORD]" in result
        finally:
            del os.environ["DS_HOST"]
            del os.environ["DS_PASSWORD"]

    def test_scrub_skips_short_values(self):
        """Values of 8 characters or fewer are not scrubbed (e.g. port numbers)."""
        import os
        from anton.chat import _DS_SECRET_VARS, _scrub_credentials

        _DS_SECRET_VARS.add("DS_PASSWORD")
        os.environ["DS_PASSWORD"] = "short"  # 5 chars — under threshold
        try:
            result = _scrub_credentials("password=short")
            assert "short" in result
        finally:
            del os.environ["DS_PASSWORD"]
            _DS_SECRET_VARS.discard("DS_PASSWORD")

    def test_scrub_fallback_redacts_unknown_long_ds_vars(self):
        """Long DS_* vars not in _DS_SECRET_VARS are scrubbed as a safety fallback."""
        import os
        from anton.chat import _scrub_credentials

        # _DS_SECRET_VARS is empty (cleared in setup_method)
        os.environ["DS_WEBHOOK_SECRET"] = "wh_sec_abcdefgh1234"
        try:
            result = _scrub_credentials("secret=wh_sec_abcdefgh1234 here")
            assert "wh_sec_abcdefgh1234" not in result
            assert "[DS_WEBHOOK_SECRET]" in result
        finally:
            del os.environ["DS_WEBHOOK_SECRET"]

    @pytest.mark.asyncio
    async def test_register_and_scrub_on_connect(self, registry, vault_dir):
        """After _handle_connect_datasource, the new secret var is immediately scrubbed."""
        import os
        from unittest.mock import AsyncMock, MagicMock, patch

        from anton.chat import _DS_SECRET_VARS, _handle_connect_datasource, _scrub_credentials

        vault = DataVault(vault_dir=vault_dir)

        session = MagicMock()
        session._history = []
        session._cortex = None

        pad = AsyncMock()
        pad.execute = AsyncMock(
            return_value=MagicMock(stdout="ok", stderr="", error=None)
        )
        session._scratchpads.get_or_create = AsyncMock(return_value=pad)

        secret_pw = "supersecretpassword999"
        prompt_responses = iter([
            "PostgreSQL",   # engine
            "y",            # have all credentials
            "db.host.com",  # host
            "5432",         # port
            "mydb",         # database
            "alice",        # user
            secret_pw,      # password
            "public",       # schema (optional, skip)
        ])

        with (
            patch("anton.chat.DataVault", return_value=vault),
            patch("anton.chat.DatasourceRegistry", return_value=registry),
            patch("rich.prompt.Prompt.ask", side_effect=lambda *a, **kw: next(prompt_responses)),
        ):
            await _handle_connect_datasource(MagicMock(), session._scratchpads, session)

        # After connect, password should be in the secret set and scrubbed
        assert "DS_PASSWORD" in _DS_SECRET_VARS
        os.environ["DS_PASSWORD"] = secret_pw
        try:
            result = _scrub_credentials(f"error: auth failed with {secret_pw}")
            assert secret_pw not in result
            assert "[DS_PASSWORD]" in result
        finally:
            del os.environ["DS_PASSWORD"]