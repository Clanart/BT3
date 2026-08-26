# Q2778: WombatBribeManager.castVotes - delegatedPool harvestAll runs inside every cast

## Question
In wombat/WombatBribeManager.sol, castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Can an unprivileged attacker reach this through `castVotes(bool swapForBnb)` while the attacker votes in the block immediately before a known keeper cast, and drive `delegatedPool votes` out of agreement with `totalVlMgpInVote` - breaking the invariant that an optional delegate leg must not be able to block the core gauge update - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: delegatedPool harvestAll runs inside every cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: an optional delegate leg must not be able to block the core gauge update; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up the attacker votes in the block immediately before a known keeper cast, snapshot `delegatedPool votes` and `totalVlMgpInVote`, run the attacker's `castVotes(bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
