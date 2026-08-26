# Q5097: SmartWomConvert.smartConvert - maxSwapAmount derives from instantaneous cash and liability

## Question
Note that in wombat/SmartWomConvert.sol, maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Can an attacker holding only tokens bought on market reach it via `smartConvert(uint256 _amountIn, uint256 _mode)` under the router leaves a non-zero allowance after the swap and force `obtainedmWomAmount` apart from `IERC20(mWom).balanceOf(address(this))`, breaking the invariant that a protocol-owned trade ceiling must not be computed from state the caller can set in the same block for High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: maxSwapAmount derives from instantaneous cash and liability)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: a protocol-owned trade ceiling must not be computed from state the caller can set in the same block; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under the router leaves a non-zero allowance after the swap, asserting on every row that a protocol-owned trade ceiling must not be computed from state the caller can set in the same block.
