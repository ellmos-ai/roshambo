# roshambo-fieldkit — task list

Twelve small, independent helper functions. Each one is a separate task with a
separate module and its own tests. Nothing here depends on anything else here, so
any task can be done at any time by anyone — which is exactly why the only thing
stopping two agents from doing the same one is the lease.

Tasks are listed in order. **Take the first one that is not yet done.**

For task `NN` create exactly two files:

- `fieldkit/<module>.py` — the function, with a one-line docstring.
- `tests/test_<module>.py` — `pytest` tests covering the examples given plus the
  edge case named in the spec.

Pure Python 3, standard library only, no imports from other fieldkit modules.

---

## 01 — `human_duration` · module `human_duration`

`human_duration(seconds: int) -> str` renders a whole number of seconds compactly.

- `human_duration(0)` → `"0s"`
- `human_duration(45)` → `"45s"`
- `human_duration(125)` → `"2m 5s"`
- `human_duration(3600)` → `"1h"`
- `human_duration(3725)` → `"1h 2m 5s"`

Units with a zero value are omitted, except that zero itself renders `"0s"`.
Edge case to test: a value that is an exact number of hours.

## 02 — `parse_resource` · module `parse_resource`

`parse_resource(name: str) -> tuple[str, str, str]` splits a Roshambo resource name
of the form `kind:scope:path` into its three parts.

- `parse_resource("repo:roshambo:src/memory.py")` → `("repo", "roshambo", "src/memory.py")`
- `parse_resource("table:public:trails")` → `("table", "public", "trails")`

The path part may itself contain colons and must not be split further.
Edge case to test: fewer than three parts raises `ValueError`.

## 03 — `backoff_delays` · module `backoff_delays`

`backoff_delays(attempts: int, base: float = 1.0, cap: float = 60.0) -> list[float]`
returns exponential delays, each capped.

- `backoff_delays(4)` → `[1.0, 2.0, 4.0, 8.0]`
- `backoff_delays(3, base=0.5)` → `[0.5, 1.0, 2.0]`
- `backoff_delays(5, base=10, cap=25)` → `[10.0, 20.0, 25.0, 25.0, 25.0]`

Edge case to test: `attempts` of 0 returns an empty list.

## 04 — `truncate_middle` · module `truncate_middle`

`truncate_middle(text: str, limit: int) -> str` shortens `text` to at most `limit`
characters by removing the middle and inserting a single `…` (U+2026).

- `truncate_middle("abcdefghij", 10)` → `"abcdefghij"` (already short enough)
- `truncate_middle("abcdefghij", 7)` → `"abc…hij"`

The result must never be longer than `limit`.
Edge case to test: a `limit` below 3 raises `ValueError`.

## 05 — `is_expired` · module `is_expired`

`is_expired(expires_at: datetime, now: datetime) -> bool` reports whether a lease has
lapsed. Both arguments are timezone-aware `datetime` objects.

- expiry one second in the future → `False`
- expiry one second in the past → `True`
- expiry exactly equal to `now` → `True` (the boundary counts as expired)

Edge case to test: a naive (timezone-less) argument raises `ValueError`.

## 06 — `slugify` · module `slugify`

`slugify(text: str) -> str` turns a title into a lowercase hyphenated slug.

- `slugify("Hello World")` → `"hello-world"`
- `slugify("  Multi   Space  ")` → `"multi-space"`
- `slugify("Already-Slugged")` → `"already-slugged"`

Non-alphanumeric characters become separators; runs of separators collapse to one;
leading and trailing hyphens are stripped.
Edge case to test: a string with no alphanumeric characters returns `""`.

## 07 — `chunk` · module `chunk`

`chunk(items: list, size: int) -> list[list]` splits a list into consecutive chunks.

- `chunk([1,2,3,4,5], 2)` → `[[1,2],[3,4],[5]]`
- `chunk([], 3)` → `[]`

Edge case to test: a `size` of 0 or less raises `ValueError`.

## 08 — `percent` · module `percent`

`percent(part: float, whole: float, digits: int = 1) -> float` returns the percentage
`part` is of `whole`, rounded to `digits` decimal places.

- `percent(1, 4)` → `25.0`
- `percent(1, 3)` → `33.3`
- `percent(2, 3, digits=2)` → `66.67`

Edge case to test: a `whole` of 0 returns `0.0` rather than raising.

## 09 — `ordinal` · module `ordinal`

`ordinal(n: int) -> str` renders an English ordinal.

- `ordinal(1)` → `"1st"`, `ordinal(2)` → `"2nd"`, `ordinal(3)` → `"3rd"`
- `ordinal(4)` → `"4th"`, `ordinal(21)` → `"21st"`

Edge case to test: 11, 12 and 13 are `"11th"`, `"12th"`, `"13th"`, not `"11st"` etc.

## 10 — `merge_intents` · module `merge_intents`

`merge_intents(intents: list[str]) -> str` joins intent strings with `"; "`, removing
duplicates and blanks while preserving first-seen order.

- `merge_intents(["fix bug", "fix bug", "write docs"])` → `"fix bug; write docs"`
- `merge_intents(["a", "  ", "b"])` → `"a; b"`

Whitespace around each intent is stripped before comparison.
Edge case to test: an empty list returns `""`.

## 11 — `clamp` · module `clamp`

`clamp(value: float, low: float, high: float) -> float` restricts a value to a range.

- `clamp(5, 0, 10)` → `5`
- `clamp(-1, 0, 10)` → `0`
- `clamp(11, 0, 10)` → `10`

Edge case to test: `low` greater than `high` raises `ValueError`.

## 12 — `format_table` · module `format_table`

`format_table(rows: list[list[str]]) -> str` renders rows as a plain text table with
columns padded to the widest cell and joined by two spaces. Rows are joined by `\n`,
with no trailing newline.

- `format_table([["a","bb"],["ccc","d"]])` → `"a    bb\nccc  d "`

Trailing padding on the last column is kept, so every line has the same width.
Edge case to test: an empty list of rows returns `""`.
