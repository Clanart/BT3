# Q4038: WombatBribeManager.castVotes - castVotes scales pool votes by a denominator that omits delegated votes

## Question
In wombat/WombatBribeManager.sol, castVotes() computes targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote, where the numerator includes delegated-pool votes and the denominator excludes them, so the requested veWOM allocation across pools does not sum to the veWOM actually held. Does `castVotes(bool swapForBnb)` let an unprivileged caller exploit that under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, so that `userVotedForPoolInVlmgp[user][lp]` diverges from `IBribeRewardPool(pool.rewarder).balanceOf(user)`, the invariant that the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes scales pool votes by a denominator that omits delegated votes)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() computes targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote, where the numerator includes delegated-pool votes and the denominator excludes them, so the requested veWOM allocation across pools does not sum to the veWOM actually held. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, call `castVotes(bool swapForBnb)`, and assert `userVotedForPoolInVlmgp[user][lp]` equals `IBribeRewardPool(pool.rewarder).balanceOf(user)` and that no account can withdraw more than it put in.
