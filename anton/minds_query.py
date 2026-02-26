"""MindsDB query client for running native queries and discovering data catalogs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class MindsQueryClient:
    """REST client to run native queries via a MindsDB Mind and retrieve results as DataFrames.

    Public surface:
      - get_data_catalog()         discover available datasources, tables, columns
      - run_native_query_df()      run a native query against a datasource

    Internal/private:
      - _run_query_df()            (used by run_native_query_df)
    """

    mindsserver_url: str
    api_key: str
    mind_name: str
    timeout_s: float = 60.0
    verify_ssl: bool = True
    progress_fn: Callable[..., object] | None = field(default=None, repr=False)

    # ── HTTP helpers ─────────────────────────────────────────────

    def _client(self):  # -> httpx.Client
        import httpx
        return httpx.Client(
            base_url=self.mindsserver_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            follow_redirects=True,
            timeout=self.timeout_s,
            verify=self.verify_ssl,
        )

    # ── Datasources + Data Catalog ───────────────────────────────

    def _list_datasources(self, client) -> list[dict[str, Any]]:
        r = client.get("/api/v1/datasources")
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected datasources payload: {type(data)}")
        return data

    def _get_datasource_catalog(
        self,
        client,
        datasource_name: str,
        *,
        mind_filter: str | None = None,
    ) -> dict[str, Any]:
        params = {}
        if mind_filter:
            params["mind"] = mind_filter
        r = client.get(f"/api/v1/datasources/{datasource_name}/catalog", params=params)
        if r.status_code == 404:
            return {
                "datasource": {"name": datasource_name},
                "tables": [],
                "status": {"overall_status": "NOT_FOUND", "message": "Catalog not found for datasource"},
            }
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected catalog payload: {type(payload)}")
        return payload

    def _get_mind_details(self, client) -> dict[str, Any]:
        """Fetch Mind metadata from GET /api/v1/minds/{mind_name}."""
        r = client.get(f"/api/v1/minds/{self.mind_name}")
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected mind details payload: {type(payload)}")
        return payload

    def get_data_catalog(self) -> dict[str, Any]:
        """Return a mind data catalog: datasources + tables/columns metadata + mind prompts.

        Shape::

            {
              "mind": "<mind_name>",
              "system_prompt": "...",
              "prompt_template": "...",
              "datasources": [
                 {
                   "name": "...",
                   "engine": "...",
                   "description": "...",
                   "tables_allowlist": [...],
                   "catalog": { ... }
                 },
                 ...
              ]
            }
        """
        with self._client() as client:
            # Fetch mind-level details (system_prompt, prompt_template, datasources)
            mind_details = self._get_mind_details(client)
            params = mind_details.get("parameters") or {}
            system_prompt = params.get("system_prompt", "")
            prompt_template = params.get("prompt_template", "")

            # Only include datasources actually attached to this mind.
            # The mind details response has a "datasources" list with the
            # attached names — use it as a filter so the LLM doesn't try
            # to query datasources the mind can't reach.
            mind_ds_list = mind_details.get("datasources") or []
            mind_ds_names: set[str] = set()
            for entry in mind_ds_list:
                if isinstance(entry, dict):
                    n = entry.get("name")
                elif isinstance(entry, str):
                    n = entry
                else:
                    continue
                if n:
                    mind_ds_names.add(n)

            all_datasources = self._list_datasources(client)

            # If the mind has an explicit datasource list, filter to only those.
            # Otherwise fall back to all (for minds without explicit attachments).
            if mind_ds_names:
                datasources = [
                    ds for ds in all_datasources
                    if isinstance(ds, dict) and ds.get("name") in mind_ds_names
                ]
            else:
                datasources = all_datasources

            out: list[dict[str, Any]] = []
            for ds in datasources:
                if not isinstance(ds, dict):
                    continue
                name = ds.get("name")
                if not name:
                    continue
                engine = ds.get("engine")
                description = ds.get("description")
                tables_allowlist = ds.get("tables") or []
                catalog = self._get_datasource_catalog(client, name, mind_filter=self.mind_name)
                if isinstance(catalog, dict):
                    cat_ds = catalog.get("datasource") or {}
                    if isinstance(cat_ds, dict) and not cat_ds.get("engine") and engine:
                        cat_ds["engine"] = engine
                        catalog["datasource"] = cat_ds
                out.append(
                    {
                        "name": name,
                        "engine": engine,
                        "description": description,
                        "tables_allowlist": tables_allowlist,
                        "catalog": catalog,
                    }
                )
            result: dict[str, Any] = {"mind": self.mind_name, "datasources": out}
            if system_prompt:
                result["system_prompt"] = system_prompt
            if prompt_template:
                result["prompt_template"] = prompt_template
            return result

    # ── Conversation management ──────────────────────────────────

    def _ensure_conversation(self, client) -> str:
        """Get or create a dedicated conversation for anton queries.

        Looks for an existing conversation with metadata.topic == "anton queries".
        Creates one if not found. Returns the conversation ID.
        """
        r = client.get("/api/v1/conversations/")
        r.raise_for_status()
        convs = r.json()
        if isinstance(convs, list):
            for c in convs:
                if not isinstance(c, dict):
                    continue
                meta = c.get("metadata") or {}
                if meta.get("topic") == "anton queries" and c.get("id"):
                    return c["id"]

        # Not found — create one
        r = client.post(
            "/api/v1/conversations",
            json={"metadata": {"topic": "anton queries"}},
        )
        r.raise_for_status()
        new_conv = r.json()
        conv_id = new_conv.get("id")
        if not conv_id:
            raise RuntimeError(f"Created conversation but got no id: {new_conv}")
        return conv_id

    # ── Query execution (private) ────────────────────────────────

    def _run_query_df(self, sql: str):
        """Execute a SQL string via the Mind and return a DataFrame."""
        from io import StringIO
        import pandas as pd

        with self._client() as client:
            conv_id = self._ensure_conversation(client)

            if self.progress_fn:
                self.progress_fn("submitting query...")

            r = client.post(
                "/api/v1/responses/",
                json={
                    "model": self.mind_name,
                    "input": sql,
                    "conversation": conv_id,
                    "stream": False,
                },
            )
            r.raise_for_status()
            resp = r.json()

            # Try structured query_result first
            output_items = resp.get("output") or []
            for item in output_items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "query_result":
                    cols = item.get("column_names") or []
                    rows = item.get("data") or []
                    return pd.DataFrame(rows, columns=cols)

            # Fallback: find a message with an id and try /export
            for item in output_items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "message" and item.get("id"):
                    try:
                        export_r = client.get(
                            f"/api/v1/conversations/{conv_id}/items/{item['id']}/export"
                        )
                        export_r.raise_for_status()
                        return pd.read_csv(StringIO(export_r.text))
                    except Exception:
                        continue

            # Neither worked — raise with whatever text the Mind returned
            texts = []
            for item in output_items:
                if isinstance(item, dict):
                    content = item.get("content") or item.get("text") or ""
                    if content:
                        texts.append(str(content))
            detail = "; ".join(texts) if texts else str(resp)
            raise RuntimeError(f"No query result in Mind response: {detail}")

    # ── Public: native queries ───────────────────────────────────

    def run_native_query_df(
        self,
        native_query: str,
        datasource_name: str,
    ):
        """Run a native query against a datasource and return the result as a DataFrame.

        Wraps native_query as::

            SELECT * FROM <datasource_name>('<native_query>');
        """
        nq = native_query.strip().rstrip(";")
        nq_escaped = nq.replace("'", "''")
        wrapped = f"SELECT * FROM {datasource_name}('{nq_escaped}')"
        return self._run_query_df(wrapped)
