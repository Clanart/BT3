# Q5061: WombatBribeManager.castVotes - castVotes scales pool votes by a denominator that omits delegated votes

## Question
wombat/WombatBribeManager.sol - castVotes() computes targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote, where the numerator includes delegated-pool votes and the denominator excludes them, so the requested veWOM allocation across pools does not sum to the veWOM actually held. Can an unprivileged attacker controlling the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination, under the attacker passes the same lp address several times in one array, exploit this through `castVotes(bool swapForBnb)` to break the reconciliation between `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp` and the invariant that the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds, yielding Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes scales pool votes by a denominator that omits delegated votes)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() computes targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote, where the numerator includes delegated-pool votes and the denominator excludes them, so the requested veWOM allocation across pools does not sum to the veWOM actually held. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `castVotes(bool swapForBnb)` sequence atomically under the attacker passes the same lp address several times in one array, asserting at the end that `getVoteForLp(lp) from the Wombat voter` still equals `poolInfos[lp].totalVoteInVlmgp` and the PoC's balance delta is non-positive.
