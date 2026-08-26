# Q5612: WombatBribeManager.castVotes - castVotes pays the caller fee to whoever calls first

## Question
In wombat/WombatBribeManager.sol, _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Can an unprivileged attacker reach this through `castVotes(bool swapForBnb)` while the attacker passes an lp address that was never registered in poolInfos, and drive `userVotedForPoolInVlmgp[user][lp]` out of agreement with `IBribeRewardPool(pool.rewarder).balanceOf(user)` - breaking the invariant that a fee intended to compensate a keeper must not be capturable by an actor who adds no value - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes pays the caller fee to whoever calls first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: _forwardRewards() transfers every non-zero feeAmount to msg.sender, and castVotes() is permissionless, so the entire bribe caller fee for a cast goes to whichever address lands the transaction. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: a fee intended to compensate a keeper must not be capturable by an actor who adds no value; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `castVotes(bool swapForBnb)`: constrain the setup so that the attacker passes an lp address that was never registered in poolInfos, fuzz the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination), and assert after every call that a fee intended to compensate a keeper must not be capturable by an actor who adds no value.
