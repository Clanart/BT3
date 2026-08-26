# Q2355: MasterMagpie.multiclaim - lpSupply inflation by direct token donation

## Question
rewards/MasterMagpie.sol - _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Can an unprivileged attacker controlling the full _stakingTokens array, including duplicates and unregistered addresses, under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, exploit this through `multiclaim(address[] _stakingTokens)` to break the reconciliation between `_calLpSupply(_stakingToken)` and `IERC20(_stakingToken).balanceOf(masterMagpie)` and the invariant that MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: lpSupply inflation by direct token donation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `multiclaim(address[] _stakingTokens)` sequence atomically under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, asserting at the end that `_calLpSupply(_stakingToken)` still equals `IERC20(_stakingToken).balanceOf(masterMagpie)` and the PoC's balance delta is non-positive.
