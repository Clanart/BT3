# Q5893: WombatBribeManager.castVotes - castVotes scales pool votes by a denominator that omits delegated votes

## Question
wombat/WombatBribeManager.sol - castVotes() computes targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote, where the numerator includes delegated-pool votes and the denominator excludes them, so the requested veWOM allocation across pools does not sum to the veWOM actually held. Can an unprivileged attacker controlling the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination, under the attacker has just cancelled a cooldown so getUserVotable jumped upward, exploit this through `castVotes(bool swapForBnb)` to break the reconciliation between `poolInfos[lp].totalVoteInVlmgp` and `totalVlMgpInVote` and the invariant that the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds, yielding Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes scales pool votes by a denominator that omits delegated votes)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() computes targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote, where the numerator includes delegated-pool votes and the denominator excludes them, so the requested veWOM allocation across pools does not sum to the veWOM actually held. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has just cancelled a cooldown so getUserVotable jumped upward, call `castVotes(bool swapForBnb)`, and assert `poolInfos[lp].totalVoteInVlmgp` equals `totalVlMgpInVote` and that no account can withdraw more than it put in.
