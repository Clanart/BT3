# Q4204: MasterMagpie.updatePool - lpSupply inflation by direct token donation

## Question
rewards/MasterMagpie.sol: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. With _stakingToken and the timestamp at which accMGPPerShare is rolled forward under attacker control and the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, can an unprivileged caller sequence `updatePool(address _stakingToken)` so that `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)` no longer reconcile, violating the invariant that MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lpSupply inflation by direct token donation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty.
- Invariant to test: MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, snapshot `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)`, run the attacker's `updatePool(address _stakingToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
