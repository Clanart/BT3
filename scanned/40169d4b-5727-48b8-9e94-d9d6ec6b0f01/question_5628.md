# Q5628: WombatBribeManager.castVotes - delegatedPool harvestAll runs inside every cast

## Question
Consider wombat/WombatBribeManager.sol, where castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Assuming the attacker passes an lp address that was never registered in poolInfos, can an unprivileged attacker turn this into a divergence between `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` via `castVotes(bool swapForBnb)`, breaking the invariant that an optional delegate leg must not be able to block the core gauge update and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: delegatedPool harvestAll runs inside every cast)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() ends with IDelegateVoteRewardPool(delegatedPool).harvestAll(), so any revert inside the delegate pool's reward handling blocks every cast and therefore the whole gauge from being updated. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: an optional delegate leg must not be able to block the core gauge update; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up the attacker passes an lp address that was never registered in poolInfos, snapshot `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)`, run the attacker's `castVotes(bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
