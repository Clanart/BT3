# Q3196: BribeRewardPool.withdrawFor - queued backlog while totalSupply is zero

## Question
Consider rewards/BribeRewardPool.sol, where _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Assuming the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, breaking the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, call `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, and assert `userRewards[_rewardToken][account]` equals `earned(account,_rewardToken)` and that no account can withdraw more than it put in.
