# Q5410: MasterMagpie.updatePool - lpSupply inflation by direct token donation

## Question
rewards/MasterMagpie.sol: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), is there an unprivileged sequence of `updatePool(address _stakingToken)` that leaves `unClaimedMgp[_stakingToken][user]` unreconciled with `userInfo[_stakingToken][user].rewardDebt`, violates the invariant that MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lpSupply inflation by direct token donation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), have the attacker run `updatePool(address _stakingToken)`, then assert the victim's claimable value and the `unClaimedMgp[_stakingToken][user]` versus `userInfo[_stakingToken][user].rewardDebt` relation are unchanged by the attacker's transaction.
