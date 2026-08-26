# Q5057: SmartWomConvert.smartConvert - _convertRatio is fully attacker-chosen

## Question
In wombat/SmartWomConvert.sol, _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Can an unprivileged attacker reach this through `smartConvert(uint256 _amountIn, uint256 _mode)` while the router leaves a non-zero allowance after the swap, and drive `obtainedmWomAmount` out of agreement with `IERC20(mWom).balanceOf(address(this))` - breaking the invariant that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: _convertRatio is fully attacker-chosen)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `smartConvert(uint256 _amountIn, uint256 _mode)`: constrain the setup so that the router leaves a non-zero allowance after the swap, fuzz the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from), and assert after every call that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path.
