# Q1819: BribeRewardPool.stakeFor - queued backlog while totalSupply is zero

## Question
In rewards/BribeRewardPool.sol, _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Can an unprivileged attacker reach this through `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` while the bribe token registered for this gauge charges a transfer fee, and drive `rewards[_rewardToken].queuedRewards` out of agreement with `totalSupply at the moment of the flush` - breaking the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the bribe token registered for this gauge charges a transfer fee, snapshot `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush`, run the attacker's `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
