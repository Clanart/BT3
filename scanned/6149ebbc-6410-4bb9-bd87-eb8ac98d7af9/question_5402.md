# Q5402: WombatBribeManager.castVotes - delegatedPool harvestAll runs inside every cast

## Question
In wombat/WombatBribeManager.sol, castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Can an unprivileged attacker reach this through `castVotes(bool swapForBnb)` while the attacker passes offsetting positive and negative deltas that net to zero, and drive `userVotedForPoolInVlmgp[user][lp]` out of agreement with `IBribeRewardPool(pool.rewarder).balanceOf(user)` - breaking the invariant that an optional delegate leg must not be able to block the core gauge update - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: delegatedPool harvestAll runs inside every cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: an optional delegate leg must not be able to block the core gauge update; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `castVotes(bool swapForBnb)` sequence atomically under the attacker passes offsetting positive and negative deltas that net to zero, asserting at the end that `userVotedForPoolInVlmgp[user][lp]` still equals `IBribeRewardPool(pool.rewarder).balanceOf(user)` and the PoC's balance delta is non-positive.
