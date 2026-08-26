# Q5743: WombatBribeManager.castVotes - delegatedPool harvestAll runs inside every cast

## Question
In wombat/WombatBribeManager.sol, castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Does `castVotes(bool swapForBnb)` let an unprivileged caller exploit that under the bribe contract for the pool registers more than one reward token, so that `getVoteForLp(lp) from the Wombat voter` diverges from `poolInfos[lp].totalVoteInVlmgp`, the invariant that an optional delegate leg must not be able to block the core gauge update is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: delegatedPool harvestAll runs inside every cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: an optional delegate leg must not be able to block the core gauge update; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `castVotes(bool swapForBnb)` sequence atomically under the bribe contract for the pool registers more than one reward token, asserting at the end that `getVoteForLp(lp) from the Wombat voter` still equals `poolInfos[lp].totalVoteInVlmgp` and the PoC's balance delta is non-positive.
