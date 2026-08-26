# Q2999: SmartWomConvert.smartConvert - maxSwapAmount derives from instantaneous cash and liability

## Question
wombat/SmartWomConvert.sol - maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Can an unprivileged attacker controlling _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from, under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, exploit this through `smartConvert(uint256 _amountIn, uint256 _mode)` to break the reconciliation between `_convertRatio` and `DENOMINATOR` and the invariant that a protocol-owned trade ceiling must not be computed from state the caller can set in the same block, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: maxSwapAmount derives from instantaneous cash and liability)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Precondition: the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn.
- Invariant to test: a protocol-owned trade ceiling must not be computed from state the caller can set in the same block; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, then assert `_convertRatio` and `DENOMINATOR` end identical in both runs.
