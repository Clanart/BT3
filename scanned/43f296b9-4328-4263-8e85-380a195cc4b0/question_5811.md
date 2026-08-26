# Q5811: WombatBribeManager.castVotes - castVotes scales pool votes by a denominator that omits delegated votes

## Question
Consider wombat/WombatBribeManager.sol, where castVotes() computes targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote, where the numerator includes delegated-pool votes and the denominator excludes them, so the requested veWOM allocation across pools does not sum to the veWOM actually held. Assuming the victim has a large unsettled balance in the pool rewarder, can an unprivileged attacker turn this into a divergence between `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` via `castVotes(bool swapForBnb)`, breaking the invariant that the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes scales pool votes by a denominator that omits delegated votes)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() computes targetVote = pool.totalVoteInVlmgp * totalVotes() / totalVlMgpInVote, where the numerator includes delegated-pool votes and the denominator excludes them, so the requested veWOM allocation across pools does not sum to the veWOM actually held. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `castVotes(bool swapForBnb)`: constrain the setup so that the victim has a large unsettled balance in the pool rewarder, fuzz the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination), and assert after every call that the veWOM allocation requested across all pools must sum to at most the veWOM the protocol holds.
