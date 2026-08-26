# Q5353: WombatBribeManager.castVotes - castVotes scales pool votes by a denominator that omits delegated votes

## Question
In wombat/WombatBribeManager.sol, castVotes() computes targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote, where the numerator includes delegated-pool votes and the denominator excludes them, so the requested veWOM allocation across pools does not sum to the veWOM actually held. Can an unprivileged attacker reach this through `castVotes(bool swapForBnb)` while the attacker passes offsetting positive and negative deltas that net to zero, and drive `poolInfos[lp].isActive` out of agreement with `userVotedForPoolInVlmgp[user][lp]` - breaking the invariant that the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds - for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes scales pool votes by a denominator that omits delegated votes)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() computes targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote, where the numerator includes delegated-pool votes and the denominator excludes them, so the requested veWOM allocation across pools does not sum to the veWOM actually held. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker passes offsetting positive and negative deltas that net to zero, then assert `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]` end identical in both runs.
