# Q5758: MasterMagpie.deposit - lpSupply inflation by direct token donation

## Question
In rewards/MasterMagpie.sol, _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Does `deposit(address _stakingToken, uint256 _amount)` let an unprivileged caller exploit that under the victim has a large unClaimedMgp balance that has not been settled for several epochs, so that `userInfo[_stakingToken][user].amount` diverges from `_calLpSupply(_stakingToken)`, the invariant that MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: lpSupply inflation by direct token donation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_stakingToken, _amount, and the ERC20 the pool was registered with) under the victim has a large unClaimedMgp balance that has not been settled for several epochs, asserting on every row that MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply.
