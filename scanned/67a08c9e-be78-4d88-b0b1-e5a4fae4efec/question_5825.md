# Q5825: WombatBribeManager.castVotes - delegatedPool harvestAll runs inside every cast

## Question
wombat/WombatBribeManager.sol: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Under the victim has a large unsettled balance in the pool rewarder, is there an unprivileged sequence of `castVotes(bool swapForBnb)` that leaves `poolInfos[lp].isActive` unreconciled with `userVotedForPoolInVlmgp[user][lp]`, violates the invariant that an optional delegate leg must not be able to block the core gauge update, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: delegatedPool harvestAll runs inside every cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: an optional delegate leg must not be able to block the core gauge update; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has a large unsettled balance in the pool rewarder, then assert `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]` end identical in both runs.
