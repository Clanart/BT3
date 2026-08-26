# Q4610: SmartWomConvert.convertFor - safeApprove without reset on the router leg

## Question
Consider wombat/SmartWomConvert.sol, where _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Assuming the attacker sandwiches the transaction on the wom/mWom Wombat pool, can an unprivileged attacker turn this into a divergence between `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))` via `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`, breaking the invariant that an approval on a repeated path must be idempotent and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: safeApprove without reset on the router leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` sequence atomically under the attacker sandwiches the transaction on the wom/mWom Wombat pool, asserting at the end that `obtainedmWomAmount` still equals `IERC20(mWom).balanceOf(address(this))` and the PoC's balance delta is non-positive.
