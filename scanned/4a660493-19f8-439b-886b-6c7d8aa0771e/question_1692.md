# Q1692: SmartWomConvert.smartConvert - maxSwapAmount derives from instantaneous cash and liability

## Question
wombat/SmartWomConvert.sol: maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. With _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from under attacker control and the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, can an unprivileged caller sequence `smartConvert(uint256 _amountIn, uint256 _mode)` so that `_minRec` and `convertAmount + amountRec` no longer reconcile, violating the invariant that a protocol-owned trade ceiling must not be computed from state the caller can set in the same block and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: maxSwapAmount derives from instantaneous cash and liability)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Precondition: the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs.
- Invariant to test: a protocol-owned trade ceiling must not be computed from state the caller can set in the same block; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, then assert `_minRec` and `convertAmount + amountRec` end identical in both runs.
