# Q2387: SmartWomConvert.smartConvert - maxSwapAmount derives from instantaneous cash and liability

## Question
In wombat/SmartWomConvert.sol, maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Can an unprivileged attacker reach this through `smartConvert(uint256 _amountIn, uint256 _mode)` while womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, and drive `obtainedmWomAmount` out of agreement with `IERC20(mWom).balanceOf(address(this))` - breaking the invariant that a protocol-owned trade ceiling must not be computed from state the caller can set in the same block - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: maxSwapAmount derives from instantaneous cash and liability)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Precondition: womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR.
- Invariant to test: a protocol-owned trade ceiling must not be computed from state the caller can set in the same block; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, asserting on every row that a protocol-owned trade ceiling must not be computed from state the caller can set in the same block.
