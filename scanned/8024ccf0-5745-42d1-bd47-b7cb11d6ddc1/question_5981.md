# Q5981: MasterMagpie.updatePool - lpSupply inflation by direct token donation

## Question
Consider rewards/MasterMagpie.sol, where _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Assuming the attacker repeats the call in the same block to observe the second, no-op iteration, can an unprivileged attacker turn this into a divergence between `vlmgp.totalSupply()` and `sum of userInfo[vlmgp][*].amount` via `updatePool(address _stakingToken)`, breaking the invariant that MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lpSupply inflation by direct token donation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker repeats the call in the same block to observe the second, no-op iteration, snapshot `vlmgp.totalSupply()` and `sum of userInfo[vlmgp][*].amount`, run the attacker's `updatePool(address _stakingToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
