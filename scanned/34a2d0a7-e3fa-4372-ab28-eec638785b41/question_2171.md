# Q2171: MasterMagpie.massUpdatePools - lpSupply inflation by direct token donation

## Question
rewards/MasterMagpie.sol: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. With the block in which every registered pool is rolled forward at once under attacker control and the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, can an unprivileged caller sequence `massUpdatePools()` so that `vlmgp.totalSupply()` and `sum of userInfo[vlmgp][*].amount` no longer reconcile, violating the invariant that MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `massUpdatePools()` (mechanism: lpSupply inflation by direct token donation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `massUpdatePools()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the block in which every registered pool is rolled forward at once
- Exploit idea: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, call `massUpdatePools()`, and assert `vlmgp.totalSupply()` equals `sum of userInfo[vlmgp][*].amount` and that no account can withdraw more than it put in.
