# Q3501: WombatBribeManager.castVotes - delegatedPool harvestAll runs inside every cast

## Question
In wombat/WombatBribeManager.sol, castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Starting from a state where the pool the attacker voted for has been deactivated so unvote reverts, can an unprivileged EOA use `castVotes(bool swapForBnb)` to leave `earnedRewards reported by claimAllBribes` inconsistent with `the tokens actually transferred by getReward`, violating the invariant that an optional delegate leg must not be able to block the core gauge update and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: delegatedPool harvestAll runs inside every cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: an optional delegate leg must not be able to block the core gauge update; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool the attacker voted for has been deactivated so unvote reverts, then assert `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` end identical in both runs.
