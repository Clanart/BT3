# Q5405: SmartWomConvert.convertFor - _convertRatio is fully attacker-chosen

## Question
In wombat/SmartWomConvert.sol, _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Starting from a state where _convertRatio is set to DENOMINATOR so nothing is swapped, can an unprivileged EOA use `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` to leave `_convertRatio` inconsistent with `DENOMINATOR`, violating the invariant that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: _convertRatio is fully attacker-chosen)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Precondition: _convertRatio is set to DENOMINATOR so nothing is swapped.
- Invariant to test: a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`: constrain the setup so that _convertRatio is set to DENOMINATOR so nothing is swapped, fuzz the attacker inputs (_for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound), and assert after every call that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path.
