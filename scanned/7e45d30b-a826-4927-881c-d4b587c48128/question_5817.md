# Q5817: WombatBribeManager.castVotes - castVotes pays the caller fee to whoever calls first

## Question
In wombat/WombatBribeManager.sol, _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Does `castVotes(bool swapForBnb)` let an unprivileged caller exploit that under the victim has a large unsettled balance in the pool rewarder, so that `getVoteForLp(lp) from the Wombat voter` diverges from `poolInfos[lp].totalVoteInVlmgp`, the invariant that a fee intended to compensate a keeper must not be capturable by an actor who adds no value is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes pays the caller fee to whoever calls first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: a fee intended to compensate a keeper must not be capturable by an actor who adds no value; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination) under the victim has a large unsettled balance in the pool rewarder, asserting on every row that a fee intended to compensate a keeper must not be capturable by an actor who adds no value.
