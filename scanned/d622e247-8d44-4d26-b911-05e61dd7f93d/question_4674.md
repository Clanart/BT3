# Q4674: WombatBribeManager.castVotes - delegatedPool harvestAll runs inside every cast

## Question
In wombat/WombatBribeManager.sol, castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Can an unprivileged attacker reach this through `castVotes(bool swapForBnb)` while delegatedPool is unset so the delegate legs are skipped, and drive `poolInfos[lp].totalVoteInVlmgp` out of agreement with `totalVlMgpInVote` - breaking the invariant that an optional delegate leg must not be able to block the core gauge update - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: delegatedPool harvestAll runs inside every cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: an optional delegate leg must not be able to block the core gauge update; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `castVotes(bool swapForBnb)`: constrain the setup so that delegatedPool is unset so the delegate legs are skipped, fuzz the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination), and assert after every call that an optional delegate leg must not be able to block the core gauge update.
