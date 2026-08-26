# Q2033: MasterMagpie.updatePool - lpSupply inflation by direct token donation

## Question
Note that in rewards/MasterMagpie.sol, _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Can an attacker holding only tokens bought on market reach it via `updatePool(address _stakingToken)` under the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake and force `totalAllocPoint` apart from `tokenToPoolInfo[_stakingToken].allocPoint`, breaking the invariant that MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lpSupply inflation by direct token donation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, then assert `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint` end identical in both runs.
