# Q0692: SmartWomConvert.smartConvert - _convertRatio is fully attacker-chosen

## Question
In wombat/SmartWomConvert.sol, _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Starting from a state where the attacker has pushed mWom below buybackThreshold against wom in the same transaction, can an unprivileged EOA use `smartConvert(uint256 _amountIn, uint256 _mode)` to leave `amountRec from swapExactTokensForTokens` inconsistent with `convertAmount minted 1:1 by IMWom(mWom).deposit`, violating the invariant that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: _convertRatio is fully attacker-chosen)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Precondition: the attacker has pushed mWom below buybackThreshold against wom in the same transaction.
- Invariant to test: a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `smartConvert(uint256 _amountIn, uint256 _mode)` sequence atomically under the attacker has pushed mWom below buybackThreshold against wom in the same transaction, asserting at the end that `amountRec from swapExactTokensForTokens` still equals `convertAmount minted 1:1 by IMWom(mWom).deposit` and the PoC's balance delta is non-positive.
