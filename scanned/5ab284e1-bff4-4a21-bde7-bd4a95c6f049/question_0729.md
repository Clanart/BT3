# Q0729: WombatBribeManager.castVotes - delegatedPool harvestAll runs inside every cast

## Question
wombat/WombatBribeManager.sol: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, is there an unprivileged sequence of `castVotes(bool swapForBnb)` that leaves `getVoteForLp(lp) from the Wombat voter` unreconciled with `poolInfos[lp].totalVoteInVlmgp`, violates the invariant that an optional delegate leg must not be able to block the core gauge update, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: delegatedPool harvestAll runs inside every cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: an optional delegate leg must not be able to block the core gauge update; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, snapshot `getVoteForLp(lp) from the Wombat voter` and `poolInfos[lp].totalVoteInVlmgp`, run the attacker's `castVotes(bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
