# Q3615: BribeRewardPool.donateRewards - queued backlog while totalSupply is zero

## Question
Note that in rewards/BribeRewardPool.sol, _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` under the attacker calls the inherited donateRewards for the registered bribe token and force `_balances[account]` apart from `totalSupply`, breaking the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the attacker calls the inherited donateRewards for the registered bribe token.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls the inherited donateRewards for the registered bribe token, call `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`, and assert `_balances[account]` equals `totalSupply` and that no account can withdraw more than it put in.
