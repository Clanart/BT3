# Q2638: WombatBribeManager.castVotes - castVotes scales pool votes by a denominator that omits delegated votes

## Question
In wombat/WombatBribeManager.sol, castVotes() computes targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote, where the numerator includes delegated-pool votes and the denominator excludes them, so the requested veWOM allocation across pools does not sum to the veWOM actually held. Does `castVotes(bool swapForBnb)` let an unprivileged caller exploit that under the attacker votes in the block immediately before a known keeper cast, so that `poolInfos[lp].totalVoteInVlmgp` diverges from `totalVlMgpInVote`, the invariant that the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes scales pool votes by a denominator that omits delegated votes)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() computes targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote, where the numerator includes delegated-pool votes and the denominator excludes them, so the requested veWOM allocation across pools does not sum to the veWOM actually held. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination) under the attacker votes in the block immediately before a known keeper cast, asserting on every row that the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds.
