# Q5666: WombatBribeManager.claimBribeFor - claimBribeFor settles any victim at an attacker-chosen instant

## Question
In wombat/WombatBribeManager.sol, claimBribeFor(address[],address) has no access control and calls IBribeRewardPool(rewarder).getReward(_for, _for), so a third party fixes when a victim's bribe accrual is settled and their reward index advanced. Can an unprivileged attacker reach this through `claimBribeFor(address[] lps, address _for)` while the attacker passes an lp address that was never registered in poolInfos, and drive `targetVote computed in castVotes` out of agreement with `totalVotes() from veWom.balanceOf(wombatStaking)` - breaking the invariant that only the account itself may decide when its bribe accrual is settled - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimBribeFor(address[] lps, address _for)` (mechanism: claimBribeFor settles any victim at an attacker-chosen instant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimBribeFor(address[] lps, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the lp array
- Exploit idea: claimBribeFor(address[],address) has no access control and calls IBribeRewardPool(rewarder).getReward(_for, _for), so a third party fixes when a victim's bribe accrual is settled and their reward index advanced. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: only the account itself may decide when its bribe accrual is settled; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and the lp array) under the attacker passes an lp address that was never registered in poolInfos, asserting on every row that only the account itself may decide when its bribe accrual is settled.
