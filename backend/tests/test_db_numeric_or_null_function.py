"""Tests for `nptc_numeric_or_null` (issue #54, FR-13, ADR-0027).

Two concerns: that the function itself does what its name says (a NULL for
anything not castable to `numeric`, never a raised error), and the money
test ADR-0027 exists to answer - that an expression index built over it
survives a row already holding a non-castable value, which is exactly the
scenario that kills the naive `((value #>> '{}')::numeric)` expression
outright.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

#: `1e400` is a genuinely valid `numeric` literal - Postgres's `numeric` is
#: arbitrary-precision and scientific notation is accepted regardless of
#: magnitude, unlike `float`/`double precision`. Verified directly against
#: `postgres:18.6` (the version `deploy/compose.yml` pins) before writing
#: this corpus, rather than assumed - see ADR-0027's own note that
#: `pg_input_is_valid`'s behaviour, not intuition about "obviously huge
#: numbers", is what this function's cases must match.
_VALID_NUMERIC_LITERALS = ["5", "-3.14", "0", "1e400", "  5  ", "+42"]

#: Format junk `pg_input_is_valid(..., 'numeric')` rejects.
_INVALID_NUMERIC_LITERALS = ["abc", "", "1,234", "1.2.3", "NaN and more", "5 apples"]


@pytest.fixture
def fresh_engine(owner_engine: Engine, migrated: None) -> Iterator[Engine]:
    """An engine that opens a new connection for every `connect()`.

    Postgres 18 plans the body of an inlinable SQL function afresh for roughly
    the first five calls on a connection. The pooled `db` fixture has usually
    spent those calls by the time a test runs, so only a new connection
    exercises them (ADR-0027).
    """
    engine = create_engine(owner_engine.url, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("literal", _VALID_NUMERIC_LITERALS)
def test_valid_numeric_literal_casts(db: Connection, literal: str) -> None:
    actual = db.execute(text("SELECT nptc_numeric_or_null(:v)"), {"v": literal}).scalar_one()

    assert float(actual) == pytest.approx(float(literal))


@pytest.mark.integration
@pytest.mark.parametrize("literal", _INVALID_NUMERIC_LITERALS)
def test_non_castable_literal_returns_null_not_an_error(db: Connection, literal: str) -> None:
    actual = db.execute(text("SELECT nptc_numeric_or_null(:v)"), {"v": literal}).scalar_one()

    assert actual is None


@pytest.mark.integration
def test_null_input_returns_null(db: Connection) -> None:
    """`STRICT`: a NULL argument returns NULL directly, without ever
    reaching `pg_input_is_valid`."""
    actual = db.execute(text("SELECT nptc_numeric_or_null(NULL)")).scalar_one()

    assert actual is None


@pytest.mark.integration
def test_function_is_declared_immutable(db: Connection) -> None:
    """ADR-0027's own load-bearing claim: `provolatile = 'i'`, even though
    the `pg_input_is_valid` primitive it wraps is `STABLE` - safe only
    because the target type is the fixed literal `'numeric'`, per the
    ADR's argument. An expression index's expression must be `IMMUTABLE`;
    this is what makes `CREATE INDEX` accept it at all."""
    provolatile = db.execute(
        text("SELECT provolatile FROM pg_proc WHERE proname = 'nptc_numeric_or_null'")
    ).scalar_one()

    assert provolatile == "i"


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_expression_index_survives_a_non_castable_retained_value(db: Connection) -> None:
    """The scenario ADR-0027 exists to answer: a bare
    `((value #>> '{}')::numeric)` expression index fails `CREATE INDEX`
    outright the moment one retained row is not castable - this proves
    `nptc_numeric_or_null` does not have that failure mode."""
    db.execute(text("CREATE TEMPORARY TABLE t_numeric_or_null (v text)"))
    db.execute(text("INSERT INTO t_numeric_or_null (v) VALUES ('5'), ('about 5 mg'), (NULL)"))

    # Would raise here if the expression were the naive `::numeric` cast.
    db.execute(
        text("CREATE INDEX ix_t_numeric_or_null ON t_numeric_or_null (nptc_numeric_or_null(v))")
    )

    values = (
        db.execute(text("SELECT nptc_numeric_or_null(v) FROM t_numeric_or_null ORDER BY v"))
        .scalars()
        .all()
    )
    assert sorted(values, key=lambda v: (v is None, v)) == [5, None, None]


@pytest.mark.req("FR-13")
@pytest.mark.integration
@pytest.mark.parametrize("literal", _INVALID_NUMERIC_LITERALS)
def test_non_castable_literal_returns_null_on_a_fresh_connection(
    fresh_engine: Engine, literal: str
) -> None:
    """The first call on a connection must return NULL, not raise."""
    with fresh_engine.connect() as connection:
        actual = connection.execute(
            text("SELECT nptc_numeric_or_null(:v)"), {"v": literal}
        ).scalar_one()

    assert actual is None


@pytest.mark.req("FR-13")
@pytest.mark.integration
def test_create_index_survives_a_non_castable_value_on_a_fresh_connection(
    fresh_engine: Engine,
) -> None:
    """ADR-0027's scenario on the connection a reconciler would have just
    opened, where the function has not yet been called."""
    with fresh_engine.connect() as connection:
        connection.execute(text("CREATE TEMPORARY TABLE t_fresh_numeric_or_null (v text)"))
        connection.execute(
            text("INSERT INTO t_fresh_numeric_or_null (v) VALUES ('about 5 mg'), ('5'), (NULL)")
        )

        connection.execute(
            text(
                "CREATE INDEX ix_t_fresh_numeric_or_null "
                "ON t_fresh_numeric_or_null (nptc_numeric_or_null(v))"
            )
        )

        indexed = connection.execute(
            text("SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_t_fresh_numeric_or_null'")
        ).scalar_one()
    assert indexed == 1
