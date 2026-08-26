# Q5307: SmartWomConvert.smartConvert - maxSwapAmount derives from instantaneous cash and liability

## Question
In wombat/SmartWomConvert.sol, maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Can an unprivileged attacker reach this through `smartConvert(uint256 _amountIn, uint256 _mode)` while _convertRatio is set to zero so the entire input goes through the AMM, and drive `_convertRatio` out of agreement with `DENOMINATOR` - breaking the invariant that a protocol-owned trade ceiling must not be computed from state the caller can set in the same block - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: maxSwapAmount derives from instantaneous cash and liability)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: a protocol-owned trade ceiling must not be computed from state the caller can set in the same block; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up _convertRatio is set to zero so the entire input goes through the AMM, snapshot `_convertRatio` and `DENOMINATOR`, run the attacker's `smartConvert(uint256 _amountIn, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
