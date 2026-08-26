# Q1095: SmartWomConvert.convert - safeApprove without reset on the router leg

## Question
Note that in wombat/SmartWomConvert.sol, _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Can an attacker holding only tokens bought on market reach it via `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` under the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs and force `_minRec` apart from `convertAmount + amountRec`, breaking the invariant that an approval on a repeated path must be idempotent for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: safeApprove without reset on the router leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Precondition: the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` sequence atomically under the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, asserting at the end that `_minRec` still equals `convertAmount + amountRec` and the PoC's balance delta is non-positive.
