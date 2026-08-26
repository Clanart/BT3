# Q5228: MasterMagpie.multiclaim - lpSupply inflation by direct token donation

## Question
In rewards/MasterMagpie.sol, _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Does `multiclaim(address[] _stakingTokens)` let an unprivileged caller exploit that under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), so that `mgpPerSec` diverges from `IERC20(mgp).balanceOf(masterMagpie)`, the invariant that MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: lpSupply inflation by direct token donation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), have the attacker run `multiclaim(address[] _stakingTokens)`, then assert the victim's claimable value and the `mgpPerSec` versus `IERC20(mgp).balanceOf(masterMagpie)` relation are unchanged by the attacker's transaction.
