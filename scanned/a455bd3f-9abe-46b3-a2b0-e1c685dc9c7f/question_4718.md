# Q4718: SmartWomConvert.smartConvert - _convertRatio is fully attacker-chosen

## Question
In wombat/SmartWomConvert.sol, _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Does `smartConvert(uint256 _amountIn, uint256 _mode)` let an unprivileged caller exploit that under the attacker sandwiches the transaction on the wom/mWom Wombat pool, so that `_minRec` diverges from `convertAmount + amountRec`, the invariant that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: _convertRatio is fully attacker-chosen)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker sandwiches the transaction on the wom/mWom Wombat pool, have the attacker run `smartConvert(uint256 _amountIn, uint256 _mode)`, then assert the victim's claimable value and the `_minRec` versus `convertAmount + amountRec` relation are unchanged by the attacker's transaction.
