from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SemanticRepoPg:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_table(
        self,
        *,
        source_id: UUID,
        database: str,
        table: str,
        title: str | None,
        description: str | None,
        domain: str | None,
        tags: list[str] | None,
        last_run_id: UUID | None,
        engine: str | None = None,
        total_rows: int | None = None,
        total_bytes: int | None = None,
        sorting_key: str | None = None,
        partition_key: str | None = None,
        primary_key: str | None = None,
        grain: str | None = None,
        profile: dict | None = None,
    ) -> UUID:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO sem_tables (source_id, database, table_name, title,"
                    " description, domain, tags, last_run_id, engine, total_rows,"
                    " total_bytes, sorting_key, partition_key, primary_key, grain,"
                    " profile)"
                    " VALUES (:sid, :db, :tbl, :title, :desc, :domain, :tags, :rid,"
                    " :engine, :trows, :tbytes, :sk, :pk, :pmk, :grain,"
                    " CAST(:profile AS jsonb))"
                    " ON CONFLICT (source_id, database, table_name) DO UPDATE"
                    # Curated (locked) rows keep their content fields; re-profiling
                    # only refreshes them when the row hasn't been touched.
                    " SET title = CASE WHEN sem_tables.locked THEN sem_tables.title ELSE EXCLUDED.title END,"
                    "     description = CASE WHEN sem_tables.locked THEN sem_tables.description ELSE EXCLUDED.description END,"
                    "     domain = CASE WHEN sem_tables.locked THEN sem_tables.domain ELSE EXCLUDED.domain END,"
                    "     tags = CASE WHEN sem_tables.locked THEN sem_tables.tags ELSE EXCLUDED.tags END,"
                    "     last_run_id = EXCLUDED.last_run_id,"
                    "     engine = COALESCE(EXCLUDED.engine, sem_tables.engine),"
                    "     total_rows = COALESCE(EXCLUDED.total_rows, sem_tables.total_rows),"
                    "     total_bytes = COALESCE(EXCLUDED.total_bytes, sem_tables.total_bytes),"
                    "     sorting_key = COALESCE(EXCLUDED.sorting_key, sem_tables.sorting_key),"
                    "     partition_key = COALESCE(EXCLUDED.partition_key, sem_tables.partition_key),"
                    "     primary_key = COALESCE(EXCLUDED.primary_key, sem_tables.primary_key),"
                    "     grain = COALESCE(EXCLUDED.grain, sem_tables.grain),"
                    "     profile = COALESCE(EXCLUDED.profile, sem_tables.profile),"
                    "     updated_at = now()"
                    " RETURNING id"
                ),
                {
                    "sid": source_id,
                    "db": database,
                    "tbl": table,
                    "title": title,
                    "desc": description,
                    "domain": domain,
                    "tags": tags or [],
                    "rid": last_run_id,
                    "engine": engine,
                    "trows": total_rows,
                    "tbytes": total_bytes,
                    "sk": sorting_key,
                    "pk": partition_key,
                    "pmk": primary_key,
                    "grain": grain,
                    "profile": json.dumps(profile, default=str) if profile is not None else None,
                },
            )
        ).first()
        assert row is not None
        return row[0]

    async def upsert_column(
        self,
        *,
        table_id: UUID,
        name: str,
        position: int,
        data_type: str,
        description: str | None,
        semantic_role: str | None,
        null_ratio: float | None,
        distinct_count: int | None,
        total_count: int | None,
        examples: list | None,
        is_in_sorting_key: bool = False,
        is_in_partition_key: bool = False,
        is_in_primary_key: bool = False,
        value_catalog: list | None = None,
        value_range: dict | None = None,
    ) -> UUID:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO sem_columns (table_id, name, position, data_type, description,"
                    " semantic_role, null_ratio, distinct_count, total_count, examples,"
                    " is_in_sorting_key, is_in_partition_key, is_in_primary_key,"
                    " value_catalog, value_range)"
                    " VALUES (:tid, :name, :pos, :dt, :desc, :role, :nr, :dc, :tc,"
                    " CAST(:ex AS jsonb), :isk, :ipk, :ipmk,"
                    " CAST(:vc AS jsonb), CAST(:vr AS jsonb))"
                    " ON CONFLICT (table_id, name) DO UPDATE"
                    # Stats/keys/examples are facts → always refreshed. The
                    # human-facing description/role are kept when the column is
                    # locked (curated by an admin edit or the glossary).
                    " SET position = EXCLUDED.position,"
                    "     data_type = EXCLUDED.data_type,"
                    "     description = CASE WHEN sem_columns.locked THEN sem_columns.description ELSE EXCLUDED.description END,"
                    "     semantic_role = CASE WHEN sem_columns.locked THEN sem_columns.semantic_role ELSE EXCLUDED.semantic_role END,"
                    "     null_ratio = EXCLUDED.null_ratio,"
                    "     distinct_count = EXCLUDED.distinct_count,"
                    "     total_count = EXCLUDED.total_count,"
                    # Preserve-on-null, like value_catalog/value_range below: a
                    # transient example-harvest chunk failure passes examples=None
                    # for the whole chunk, and a bare EXCLUDED.examples would wipe
                    # the previously-stored examples on a re-profile. COALESCE keeps
                    # the prior values when the new harvest has none.
                    "     examples = COALESCE(EXCLUDED.examples, sem_columns.examples),"
                    "     is_in_sorting_key = EXCLUDED.is_in_sorting_key,"
                    "     is_in_partition_key = EXCLUDED.is_in_partition_key,"
                    "     is_in_primary_key = EXCLUDED.is_in_primary_key,"
                    "     value_catalog = COALESCE(EXCLUDED.value_catalog, sem_columns.value_catalog),"
                    "     value_range = COALESCE(EXCLUDED.value_range, sem_columns.value_range),"
                    "     updated_at = now()"
                    " RETURNING id"
                ),
                {
                    "tid": table_id,
                    "name": name,
                    "pos": position,
                    "dt": data_type,
                    "desc": description,
                    "role": semantic_role,
                    "nr": null_ratio,
                    "dc": distinct_count,
                    "tc": total_count,
                    "ex": json.dumps(examples, default=str) if examples is not None else None,
                    "isk": is_in_sorting_key,
                    "ipk": is_in_partition_key,
                    "ipmk": is_in_primary_key,
                    "vc": json.dumps(value_catalog, default=str) if value_catalog is not None else None,
                    "vr": json.dumps(value_range, default=str) if value_range is not None else None,
                },
            )
        ).first()
        assert row is not None
        return row[0]

    async def insert_relation(
        self,
        *,
        source_id: UUID,
        from_table_id: UUID,
        from_column_id: UUID | None,
        to_table_id: UUID,
        to_column_id: UUID | None,
        kind: str,
        confidence: float,
        reasoning: str | None,
        cardinality: str | None = None,
        match_ratio: float | None = None,
        origin: str | None = None,
    ) -> UUID:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO sem_relations (source_id, from_table_id, from_column_id,"
                    " to_table_id, to_column_id, kind, confidence, reasoning,"
                    " cardinality, match_ratio, origin)"
                    " VALUES (:sid, :ft, :fc, :tt, :tc, :kind, :conf, :reason,"
                    " :card, :mr, :origin)"
                    " RETURNING id"
                ),
                {
                    "sid": source_id,
                    "ft": from_table_id,
                    "fc": from_column_id,
                    "tt": to_table_id,
                    "tc": to_column_id,
                    "kind": kind,
                    "conf": confidence,
                    "reason": reasoning,
                    "card": cardinality,
                    "mr": match_ratio,
                    "origin": origin,
                },
            )
        ).first()
        assert row is not None
        return row[0]

    async def relation_exists(
        self, *, from_column_id: UUID, to_column_id: UUID
    ) -> bool:
        """Guard against inserting the same edge twice (deterministic + LLM)."""
        row = (
            await self.session.execute(
                text(
                    "SELECT 1 FROM sem_relations"
                    " WHERE from_column_id = :fc AND to_column_id = :tc LIMIT 1"
                ),
                {"fc": from_column_id, "tc": to_column_id},
            )
        ).first()
        return row is not None

    async def upsert_metric(
        self,
        *,
        source_id: UUID,
        name: str,
        expression: str,
        unit: str | None,
        description: str | None,
        origin: str | None = None,
    ) -> None:
        await self.session.execute(
            text(
                "INSERT INTO sem_metrics (source_id, name, expression, unit, description, origin)"
                " VALUES (:sid, :n, :e, :u, :d, :origin)"
                " ON CONFLICT (source_id, name) DO UPDATE"
                " SET expression = EXCLUDED.expression,"
                "     unit = EXCLUDED.unit,"
                "     description = EXCLUDED.description"
                # NB: origin is intentionally NOT updated on conflict. A name
                # already owned by profiling (origin NULL) keeps its origin, so
                # the glossary re-ingest clean-up never deletes a profiling row.
            ),
            {"sid": source_id, "n": name, "e": expression, "u": unit, "d": description,
             "origin": origin},
        )

    async def upsert_glossary_term(
        self,
        *,
        source_id: UUID,
        term: str,
        definition: str,
        synonyms: list[str] | None,
        origin: str | None = None,
    ) -> None:
        await self.session.execute(
            text(
                "INSERT INTO sem_glossary (source_id, term, definition, synonyms, origin)"
                " VALUES (:sid, :t, :d, :syn, :origin)"
                " ON CONFLICT (source_id, term) DO UPDATE"
                " SET definition = EXCLUDED.definition,"
                "     synonyms = EXCLUDED.synonyms"
                # origin not updated on conflict — see upsert_metric note.
            ),
            {"sid": source_id, "t": term, "d": definition, "syn": synonyms or [],
             "origin": origin},
        )

    async def delete_glossary_semantic(self, source_id: UUID) -> None:
        """Clear all glossary-origin metrics/terms/relations for a source so a
        re-ingest fully replaces them (profiling-origin rows, origin IS NULL,
        are left intact)."""
        for table in ("sem_metrics", "sem_glossary", "sem_relations"):
            await self.session.execute(
                text(f"DELETE FROM {table} WHERE source_id = :sid AND origin = 'glossary'"),
                {"sid": source_id},
            )

    async def reset_glossary_columns(self, source_id: UUID) -> None:
        """Strip glossary-added semantics keys from columns previously enriched
        from the glossary, so values dropped from the glossary don't linger.
        (The profiled description is left as-is — we can't recover the pre-glossary
        text, and it's overwritten again for columns still in the glossary.)"""
        await self.session.execute(
            text(
                "UPDATE sem_columns SET"
                " semantics = (semantics - 'value_meanings' - 'caveats' - 'source'),"
                " updated_at = now()"
                " WHERE semantics->>'source' = 'glossary'"
                " AND table_id IN (SELECT id FROM sem_tables WHERE source_id = :sid)"
            ),
            {"sid": source_id},
        )

    async def list_glossary(self, source_id: UUID) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT term, definition, synonyms FROM sem_glossary"
                    " WHERE source_id = :sid ORDER BY term"
                ),
                {"sid": source_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def list_metrics(self, source_id: UUID) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT name, expression, unit, description FROM sem_metrics"
                    " WHERE source_id = :sid ORDER BY name"
                ),
                {"sid": source_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def find_table(
        self, source_id: UUID, database: str, table: str
    ) -> UUID | None:
        row = (
            await self.session.execute(
                text(
                    "SELECT id FROM sem_tables WHERE source_id = :sid AND database = :db AND table_name = :tbl"
                ),
                {"sid": source_id, "db": database, "tbl": table},
            )
        ).first()
        return row[0] if row else None

    async def find_column(self, table_id: UUID, name: str) -> UUID | None:
        row = (
            await self.session.execute(
                text("SELECT id FROM sem_columns WHERE table_id = :tid AND name = :n"),
                {"tid": table_id, "n": name},
            )
        ).first()
        return row[0] if row else None

    async def list_tables(self, source_id: UUID) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, database, table_name, title, description, domain, tags,"
                    " confirmation_status, confirmed_at, updated_at,"
                    " engine, total_rows, sorting_key, partition_key, primary_key, grain"
                    " FROM sem_tables WHERE source_id = :sid ORDER BY database, table_name"
                ),
                {"sid": source_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def get_table(self, table_id: UUID) -> dict[str, Any] | None:
        row = (
            await self.session.execute(
                text(
                    "SELECT id, source_id, database, table_name, title, description, domain, tags,"
                    " confirmation_status, user_notes, confirmed_at, updated_at, locked"
                    " FROM sem_tables WHERE id = :id"
                ),
                {"id": table_id},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def get_columns(
        self, table_id: UUID, *, only_enabled: bool = False
    ) -> list[dict[str, Any]]:
        # only_enabled=True is the agent-facing view (excluded columns are
        # invisible). System/admin callers (profiling, synthesis, the admin UI)
        # use the default so they always see the full set, including disabled.
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, name, position, data_type, description, semantic_role,"
                    " user_notes, null_ratio, distinct_count, total_count, examples,"
                    " is_in_sorting_key, is_in_partition_key, is_in_primary_key,"
                    " value_catalog, value_range, semantics,"
                    " confirmation_status, locked, enabled FROM sem_columns"
                    " WHERE table_id = :tid"
                    + (" AND enabled = true" if only_enabled else "")
                    + " ORDER BY position"
                ),
                {"tid": table_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def get_relations(
        self, source_id: UUID, *, only_enabled: bool = False
    ) -> list[dict[str, Any]]:
        # only_enabled drops edges whose column endpoint is a disabled column, so
        # the agent (and the graph resync) never surface a join on a hidden
        # column. NULL endpoints (table-level conceptual links) are kept.
        enabled_clause = (
            " AND NOT EXISTS (SELECT 1 FROM sem_columns fc"
            "   WHERE fc.id = from_column_id AND fc.enabled = false)"
            " AND NOT EXISTS (SELECT 1 FROM sem_columns tc"
            "   WHERE tc.id = to_column_id AND tc.enabled = false)"
            if only_enabled
            else ""
        )
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, from_table_id, from_column_id, to_table_id, to_column_id,"
                    " kind, confidence, reasoning, confirmation_status,"
                    " cardinality, match_ratio"
                    " FROM sem_relations WHERE source_id = :sid" + enabled_clause
                ),
                {"sid": source_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def update_table(
        self,
        table_id: UUID,
        *,
        title: str | None = None,
        description: str | None = None,
        domain: str | None = None,
        tags: list[str] | None = None,
        user_notes: str | None = None,
        grain: str | None = None,
        lock: bool = False,
    ) -> None:
        # lock=True only for human/admin curation (manual edit, admin_edit cmd);
        # profiling passes lock=False so it doesn't accidentally lock everything.
        await self.session.execute(
            text(
                "UPDATE sem_tables SET"
                " title = COALESCE(:title, title),"
                " description = COALESCE(:desc, description),"
                " domain = COALESCE(:domain, domain),"
                " tags = COALESCE(:tags, tags),"
                " user_notes = COALESCE(:un, user_notes),"
                " grain = COALESCE(:grain, grain),"
                " locked = CASE WHEN :lock THEN true ELSE locked END,"
                " updated_at = now() WHERE id = :id"
            ),
            {
                "id": table_id,
                "title": title,
                "desc": description,
                "domain": domain,
                "tags": tags,
                "un": user_notes,
                "grain": grain,
                "lock": lock,
            },
        )

    async def get_column(self, column_id: UUID) -> dict[str, Any] | None:
        row = (
            await self.session.execute(
                text(
                    "SELECT c.id, c.table_id, c.name, c.position, c.data_type,"
                    " c.description, c.semantic_role, c.user_notes,"
                    " c.null_ratio, c.distinct_count, c.total_count, c.examples,"
                    " c.confirmation_status, c.locked, c.enabled,"
                    " t.source_id, t.database, t.table_name, t.title AS table_title,"
                    " t.description AS table_description"
                    " FROM sem_columns c JOIN sem_tables t ON t.id = c.table_id"
                    " WHERE c.id = :id"
                ),
                {"id": column_id},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def update_column(
        self,
        column_id: UUID,
        *,
        description: str | None = None,
        semantic_role: str | None = None,
        user_notes: str | None = None,
        lock: bool = False,
    ) -> None:
        await self.session.execute(
            text(
                "UPDATE sem_columns SET"
                " description = COALESCE(:desc, description),"
                " semantic_role = COALESCE(:role, semantic_role),"
                " user_notes = COALESCE(:un, user_notes),"
                " locked = CASE WHEN :lock THEN true ELSE locked END,"
                " updated_at = now() WHERE id = :id"
            ),
            {
                "id": column_id,
                "desc": description,
                "role": semantic_role,
                "un": user_notes,
                "lock": lock,
            },
        )

    async def set_column_enabled(self, column_id: UUID, enabled: bool) -> None:
        """Toggle a column's participation in downstream investigation. The row
        and its harvested facts are kept either way — this only flips visibility."""
        await self.session.execute(
            text(
                "UPDATE sem_columns SET enabled = :en, updated_at = now() WHERE id = :id"
            ),
            {"id": column_id, "en": enabled},
        )

    async def set_columns_enabled(
        self, table_id: UUID, names: list[str], enabled: bool
    ) -> int:
        """Bulk toggle by column name within a table (the column-selection gate
        and the 'disable selected' UI action). Returns rows affected."""
        if not names:
            return 0
        res = await self.session.execute(
            text(
                "UPDATE sem_columns SET enabled = :en, updated_at = now()"
                " WHERE table_id = :tid AND name = ANY(:names)"
            ),
            {"tid": table_id, "en": enabled, "names": names},
        )
        return res.rowcount or 0

    async def apply_column_description(
        self,
        column_id: UUID,
        *,
        description: str | None,
        semantic_role: str | None,
        semantics: dict | None,
    ) -> None:
        """Pass-2 result: set the LLM description, refined role, and the
        analyst-facing semantics blob. Profiling-only — skips locked columns so
        a re-profile never clobbers a human-/glossary-curated description."""
        await self.session.execute(
            text(
                "UPDATE sem_columns SET"
                " description = COALESCE(:desc, description),"
                " semantic_role = COALESCE(:role, semantic_role),"
                " semantics = COALESCE(CAST(:sem AS jsonb), semantics),"
                " updated_at = now() WHERE id = :id AND locked = false"
            ),
            {
                "id": column_id,
                "desc": description,
                "role": semantic_role,
                "sem": json.dumps(semantics, default=str) if semantics is not None else None,
            },
        )

    async def apply_glossary_column(
        self,
        column_id: UUID,
        *,
        description: str | None,
        semantics_patch: dict | None,
        examples: list[Any] | None,
    ) -> None:
        """Authoritative human enrichment from the glossary: overwrite the
        description, merge a semantics patch (e.g. ``value_meanings``) on top of
        the profiled blob (jsonb ``||`` keeps unit/pii/safe_to_* keys), and set
        examples. Marked ``locked`` so re-profiling won't overwrite it."""
        await self.session.execute(
            text(
                "UPDATE sem_columns SET"
                " description = COALESCE(:desc, description),"
                " semantics = COALESCE(semantics, '{}'::jsonb) ||"
                "   COALESCE(CAST(:patch AS jsonb), '{}'::jsonb),"
                " examples = COALESCE(CAST(:examples AS jsonb), examples),"
                " locked = true,"
                " updated_at = now() WHERE id = :id"
            ),
            {
                "id": column_id,
                "desc": description,
                "patch": json.dumps(semantics_patch, default=str, ensure_ascii=False)
                if semantics_patch
                else None,
                "examples": json.dumps(examples, default=str, ensure_ascii=False)
                if examples is not None
                else None,
            },
        )

    async def confirm_column(self, column_id: UUID, actor: str) -> None:
        await self.session.execute(
            text(
                "UPDATE sem_columns SET confirmation_status = 'confirmed', locked = true,"
                " confirmed_at = now(), confirmed_by = :a, updated_at = now()"
                " WHERE id = :id"
            ),
            {"id": column_id, "a": actor},
        )

    async def confirm_table(self, table_id: UUID, actor: str) -> None:
        await self.session.execute(
            text(
                "UPDATE sem_tables SET confirmation_status = 'confirmed', locked = true,"
                " confirmed_at = now(), confirmed_by = :a, updated_at = now() WHERE id = :id"
            ),
            {"id": table_id, "a": actor},
        )

