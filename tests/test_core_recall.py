"""Acceptance criterion 2: a stored failure is found again through different wording.

Scope note, stated up front so nothing here is over-read: these tests run with
`PlaceholderEmbedder`, which matches on shared vocabulary and character trigrams. They
demonstrate that the write path, the `VECTOR(1024)` column, the vector index and the
ranking in `recall()` work end to end against a real cluster, and that a re-phrased
query reaches the right trail. They do **not** measure semantic quality — that needs the
Bedrock embedder, exercised by `test_recall_with_the_real_embedder` below, which skips
until `roshambo.embeddings` and AWS credentials are available.
"""

from __future__ import annotations

import re

import pytest

from roshambo.config import RoshamboConfig
from roshambo.memory import Roshambo
from roshambo.models import Claim

pytestmark = pytest.mark.live


#: One dead end plus four plausible neighbours from the same project. The distractors
#: are deliberately the same shape and length as the target: a short decoy would win on
#: length alone and the test would prove nothing.
SEEDED_TRAILS = [
    {
        "topic": "rate limiting for the public API",
        "approach": "kept a per-process request counter in memory on each web server",
        "outcome": "failure",
        "evidence": (
            "under load the counters drifted apart across the servers and callers were "
            "throttled at roughly four times the intended limit"
        ),
    },
    {
        "topic": "image thumbnail generation",
        "approach": "resized uploads synchronously inside the upload handler",
        "outcome": "success",
        "evidence": (
            "median upload latency stayed under 300 ms for files below two megabytes in "
            "the staging measurements"
        ),
    },
    {
        "topic": "search result ranking",
        "approach": "sorted candidates by recency before applying the relevance score",
        "outcome": "abandoned",
        "evidence": (
            "editors reported that fresh but irrelevant articles pushed the useful "
            "results off the first page"
        ),
    },
    {
        "topic": "nightly database backup",
        "approach": "dumped the whole cluster to a single compressed archive at midnight",
        "outcome": "success",
        "evidence": (
            "the archive completed in eleven minutes and restored cleanly into an empty "
            "test cluster twice"
        ),
    },
    {
        "topic": "session storage for the web frontend",
        "approach": "stored signed session payloads in a cookie instead of server state",
        "outcome": "inconclusive",
        "evidence": (
            "worked in the browser tests but the payload approached the four kilobyte "
            "cookie ceiling for logged-in editors"
        ),
    },
]

#: The question a fresh agent would ask. It shares the situation with the failure trail
#: above but not its sentences: no phrase of three or more words occurs in both.
REPHRASED_QUERY = (
    "we are about to count requests locally on every node to cap how often a client "
    "may call us — has that gone wrong before?"
)


@pytest.fixture
def seeded(roshambo: Roshambo) -> Roshambo:
    for trail in SEEDED_TRAILS:
        roshambo.remember(agent_id="seed-agent", **trail)
    return roshambo


def _words(text: str) -> list[str]:
    """Word tokens, the same way `PlaceholderEmbedder` splits them."""
    return re.findall(r"[a-z0-9]+", text.lower())


def test_no_long_phrase_is_shared_between_query_and_target():
    """Guards the premise of the acceptance criterion: the query is really re-phrased.

    Without this, a later edit could quietly turn the "differently worded" query into a
    near-copy of the stored text and the semantic test would still pass.
    """
    target = SEEDED_TRAILS[0]
    stored = f"{target['topic']} {target['approach']} {target['evidence']}".lower().split()
    query = REPHRASED_QUERY.lower().replace("?", "").replace("—", " ").split()

    stored_trigrams = {" ".join(stored[i : i + 3]) for i in range(len(stored) - 2)}
    query_trigrams = {" ".join(query[i : i + 3]) for i in range(len(query) - 2)}
    assert stored_trigrams & query_trigrams == set()


def test_rephrased_query_finds_the_failure_at_rank_one_lexically_not_semantically(
    seeded: Roshambo,
):
    """The acceptance criterion, restated honestly for the embedder actually in use.

    What this proves: a query that shares **no phrase** with the stored trail still
    retrieves it at rank 1, ahead of four same-shaped distractors, through the real
    write path, the `VECTOR(1024)` column, the cosine vector index and the ranking in
    `recall()`.

    What this does **not** prove: semantic retrieval. `PlaceholderEmbedder` hashes word
    tokens and character trigrams, so the ranking here comes from shared vocabulary
    ("requests", "count/counters", "limit", "node/servers") and nothing else. Two
    sentences that mean the same thing in different words would not match, and this test
    would not notice. The semantic claim belongs to
    `test_recall_with_the_real_embedder`, which uses Amazon Titan embeddings and skips
    until AWS credentials are present.
    """
    hits = seeded.recall(REPHRASED_QUERY, limit=5)

    assert hits, "recall returned nothing at all"
    assert hits[0].trail.topic == "rate limiting for the public API"
    assert hits[0].trail.outcome == "failure"
    assert hits[0].distance <= hits[-1].distance


def test_the_rank_one_hit_is_explained_by_character_trigrams_not_by_words(seeded: Roshambo):
    """Pins down which lexical mechanism actually produced the rank-1 hit.

    It is **not** word matching. The query and the winning trail share exactly two
    words — "a" and "on" — and two of the distractors share two words as well. What
    separates them is the character-trigram half of the feature space: count/counter/
    counters, request/requests, server/servers, limit/limiting.

    This distinction is the whole reason the test exists. "The failure was found through
    different wording" reads like a semantic result; here it is a sub-word string
    effect, and the two must not be confused in the write-up.
    """
    hits = seeded.recall(REPHRASED_QUERY, limit=5)
    features = seeded.embedder._features

    query_features = set(features(REPHRASED_QUERY))
    query_words = {f for f in query_features if f.startswith("w:")}

    def trail_features(hit) -> set[str]:
        trail = hit.trail
        return set(features(f"{trail.topic}\n{trail.approach}\n{trail.evidence}"))

    def word_overlap(hit) -> int:
        return len(query_words & {f for f in trail_features(hit) if f.startswith("w:")})

    def total_overlap(hit) -> int:
        return len(query_features & trail_features(hit))

    winner, *rest = hits

    assert word_overlap(winner) <= max(word_overlap(h) for h in rest), (
        "whole-word overlap now separates the winner from the field; the docstring "
        "above claims it does not, so one of the two is out of date"
    )
    assert total_overlap(winner) > max(total_overlap(h) for h in rest), (
        "the top hit does not share more features than the distractors, so lexical "
        "overlap is not what ranked it and the honest description above would be wrong"
    )


def test_shared_words_between_query_and_winner_are_only_stopwords():
    """The two words in common carry no topical information whatsoever."""
    target = SEEDED_TRAILS[0]
    stored = set(_words(f"{target['topic']} {target['approach']} {target['evidence']}"))
    shared = set(_words(REPHRASED_QUERY)) & stored
    assert shared <= {"a", "an", "the", "on", "in", "of", "to", "us", "we", "that"}


def test_recall_actually_uses_the_vector_index(seeded: Roshambo):
    """Guards the pairing of distance operator and index op class.

    A bare `VECTOR INDEX (swarm_id, embedding)` is an L2 index, and CockroachDB will not
    use it for `<=>`. The query keeps returning correct rows — it just full-scans — so
    the mistake is invisible without an EXPLAIN. This test makes it visible.

    Whether the *cost-based* planner picks the index depends on table size and
    statistics freshness (v26.2 happily full-scans a five-row swarm, and it is right
    to). So the guard does not play statistics games — it **forces** the index with a
    table hint. A forced vector index either serves the operator ("vector search" in
    the plan) or CockroachDB rejects it outright ("index cannot be used for this
    query"), which turns a silent op-class mismatch into a hard, attributable failure.
    Both directions are asserted: the operator recall() uses must be servable, and the
    mismatched operator must be refused.
    """
    import psycopg.errors

    from roshambo.db import to_vector_literal
    from roshambo.memory import DISTANCE_OP

    vector = to_vector_literal(seeded.embedder.embed(REPHRASED_QUERY))

    # Direction 1: the operator recall() actually uses, served by the forced index.
    with seeded.conn.cursor() as cur:
        cur.execute(
            f"EXPLAIN SELECT trail_id FROM trails@trails_by_swarm WHERE swarm_id = %s "
            f"ORDER BY embedding {DISTANCE_OP} %s::VECTOR LIMIT 5",
            (seeded.cfg.swarm_id, vector),
        )
        plan = "\n".join(row[0] for row in cur.fetchall())
    assert "vector search" in plan, (
        f"index trails_by_swarm cannot serve recall()'s operator {DISTANCE_OP!r} — "
        f"op class mismatch?\n{plan}"
    )

    # Direction 2: the *wrong* operator must be refused by the same forced index,
    # proving the guard can actually tell the op classes apart.
    wrong_op = "<->" if DISTANCE_OP == "<=>" else "<=>"
    with pytest.raises(psycopg.errors.Error, match="cannot be used"):
        with seeded.conn.cursor() as cur:
            cur.execute(
                f"EXPLAIN SELECT trail_id FROM trails@trails_by_swarm WHERE swarm_id = %s "
                f"ORDER BY embedding {wrong_op} %s::VECTOR LIMIT 5",
                (seeded.cfg.swarm_id, vector),
            )
    seeded.conn.rollback()


def test_recall_returns_hits_ordered_by_distance(seeded: Roshambo):
    hits = seeded.recall(REPHRASED_QUERY, limit=5)
    distances = [h.distance for h in hits]
    assert distances == sorted(distances)


def test_recall_respects_the_limit(seeded: Roshambo):
    assert len(seeded.recall(REPHRASED_QUERY, limit=2)) == 2
    assert seeded.recall(REPHRASED_QUERY, limit=0) == []


def test_recall_can_be_restricted_to_failures(seeded: Roshambo):
    """Asking only for dead ends is the common agent query: "what should I avoid?"."""
    hits = seeded.recall("counting requests per node", limit=5, outcomes=["failure"])
    assert hits
    assert {h.trail.outcome for h in hits} == {"failure"}


def test_recall_on_an_empty_swarm_returns_nothing(roshambo: Roshambo):
    assert roshambo.recall("anything at all", limit=5) == []


def test_recall_does_not_cross_swarm_boundaries(cfg: RoshamboConfig, seeded: Roshambo):
    """`swarm_id` leads the primary key and the vector index; isolation must be real."""
    other = RoshamboConfig(dsn=cfg.dsn, swarm_id=cfg.swarm_id + "-neighbour")
    with Roshambo(other, embedder=seeded.embedder) as neighbour:
        assert neighbour.recall(REPHRASED_QUERY, limit=5) == []


def test_remembered_trail_round_trips(roshambo: Roshambo):
    trail = roshambo.remember(
        topic="artifact upload",
        approach="wrote the build log straight into the trail row",
        outcome="failure",
        evidence="the row exceeded the practical statement size and the insert was rejected",
        agent_id="agent-a",
        detail={"attempt": 3, "size_mb": 64},
        artifact_uri="s3://example-bucket/logs/build-3.txt",
    )
    hit = roshambo.recall("build log written into the row", limit=1)[0]

    assert hit.trail.trail_id == trail.trail_id
    assert hit.trail.detail == {"attempt": 3, "size_mb": 64}
    assert hit.trail.artifact_uri == "s3://example-bucket/logs/build-3.txt"
    assert hit.trail.agent_id == "agent-a"
    assert hit.strength == 1.0


def test_reinforcement_raises_strength(roshambo: Roshambo):
    trail = roshambo.remember(
        topic="flaky integration test",
        approach="retried the failing case three times before reporting",
        outcome="failure",
        evidence="the retry masked a real race and the bug reached production",
    )
    assert roshambo.reinforce(trail.trail_id) == 2.0
    assert roshambo.recall("retrying a flaky case", limit=1)[0].strength == 2.0


def test_reinforcing_an_unknown_trail_returns_none(roshambo: Roshambo):
    assert roshambo.reinforce("00000000-0000-0000-0000-000000000000") is None


def test_invalid_outcome_is_rejected_before_touching_the_database(roshambo: Roshambo):
    with pytest.raises(ValueError, match="outcome"):
        roshambo.remember("t", "a", "kaputt", "e")  # type: ignore[arg-type]


def test_learn_and_decide_and_status(roshambo: Roshambo):
    roshambo.remember("topic", "approach", "failure", "evidence")
    fact = roshambo.learn("counters kept per process do not add up across servers", "lesson")
    decision = roshambo.decide(
        question="where should the rate limit counter live?",
        choice="one shared counter in the database",
        rationale="per-process counters were measured to overshoot the intended limit",
        confidence="high",
        provenance="agent-inferred",
    )
    granted = roshambo.claim("repo:demo:limits.py", "agent-a", "implement the shared counter")
    assert isinstance(granted, Claim)

    status = roshambo.status()
    assert fact.fact_id and decision.decision_id
    assert status.trails == 1
    assert status.failures == 1
    assert status.facts == 1
    assert status.active_claims == 1


@pytest.mark.aws
def test_recall_with_the_real_embedder(cfg: RoshamboConfig):
    """The same criterion with Amazon Titan embeddings, once the AWS lane has landed.

    Skips — loudly, not silently — while `roshambo.embeddings` or the credentials are
    missing, so the result above is never mistaken for a semantic measurement.
    """
    embeddings = pytest.importorskip("roshambo.embeddings", reason="AWS lane has not landed yet")

    try:
        embedder = embeddings.get_embedder(cfg)
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"no usable embedder: {exc}")

    if getattr(embedder, "is_placeholder", False):
        pytest.skip("get_embedder returned a placeholder; nothing semantic to assert")

    # A Bedrock client constructs happily without credentials and only fails on the
    # first call, so probe before asserting anything.
    try:
        embedder.embed("probe")
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"embedder is not usable here: {type(exc).__name__}: {exc}")

    with Roshambo(cfg, embedder=embedder) as client:
        for trail in SEEDED_TRAILS:
            client.remember(agent_id="seed-agent", **trail)
        hits = client.recall(REPHRASED_QUERY, limit=5)

    assert hits
    assert hits[0].trail.topic == "rate limiting for the public API"
    assert hits[0].trail.outcome == "failure"
