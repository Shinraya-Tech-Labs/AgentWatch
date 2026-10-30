"""Regression tests for MAG-007 consensus with empty proposals.

Issue #541: empty/whitespace proposals embed to a zero vector and
cosine(zero, zero) == 0, so identical empty proposals never clustered — an
all-silent round was reported as maximal disagreement (N singletons). They must
be grouped as identical, and an all-empty round is not an actionable consensus.
"""

from __future__ import annotations

from panoptis.orchestration.consensus import AgentVote, detect_consensus


def test_all_empty_proposals_group_and_are_not_maximal_disagreement():
    votes = [AgentVote("a1", ""), AgentVote("a2", ""), AgentVote("a3", "")]
    r = detect_consensus(votes)
    # One group, not three singletons (the bug in #541).
    assert r.semantic_clusters == [["a1", "a2", "a3"]]
    # An all-silent round is not an actionable consensus.
    assert r.agreed is False
    assert r.majority_proposal is None


def test_whitespace_only_proposals_treated_as_empty():
    votes = [AgentVote("a1", "   "), AgentVote("a2", "\n\t"), AgentVote("a3", "")]
    r = detect_consensus(votes)
    assert r.semantic_clusters == [["a1", "a2", "a3"]]
    assert r.agreed is False


def test_empty_minority_does_not_break_real_majority():
    votes = [
        AgentVote("a1", "add a caching layer"),
        AgentVote("a2", "add a caching layer"),
        AgentVote("a3", "add a caching layer"),
        AgentVote("a4", ""),
    ]
    r = detect_consensus(votes, similarity_threshold=0.5, majority_ratio=0.6)
    assert r.agreed is True
    assert r.majority_proposal == "add a caching layer"
    assert ["a4"] in r.semantic_clusters


def test_identical_nonempty_proposals_still_cluster():
    votes = [AgentVote("a1", "hello world"), AgentVote("a2", "hello world")]
    r = detect_consensus(votes)
    assert r.semantic_clusters == [["a1", "a2"]]
    assert r.agreed is True


def test_genuine_split_still_reported_as_disagreement():
    votes = [
        AgentVote("a1", "use sqlite locally"),
        AgentVote("a2", "deploy a kubernetes cluster"),
        AgentVote("a3", "rewrite the api in rust"),
    ]
    r = detect_consensus(votes, majority_ratio=0.7)
    assert r.agreed is False
