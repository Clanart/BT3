# Q4842: MasterMagpie.withdraw - lpSupply inflation by direct token donation

## Question
Consider rewards/MasterMagpie.sol, where _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Assuming the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, can an unprivileged attacker turn this into a divergence between `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint` via `withdraw(address _stakingToken, uint256 _amount)`, breaking the invariant that MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: lpSupply inflation by direct token donation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, then assert `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint` end identical in both runs.
