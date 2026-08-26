# Q4754: SmartWomConvert.smartConvert - safeApprove without reset on the mWOM mint leg

## Question
wombat/SmartWomConvert.sol - _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Can an unprivileged attacker controlling _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from, under the attacker sandwiches the transaction on the wom/mWom Wombat pool, exploit this through `smartConvert(uint256 _amountIn, uint256 _mode)` to break the reconciliation between `currentRatio()` and `buybackThreshold` and the invariant that the mint leg must remain usable regardless of allowance residue, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: safeApprove without reset on the mWOM mint leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: the mint leg must remain usable regardless of allowance residue; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `smartConvert(uint256 _amountIn, uint256 _mode)`: constrain the setup so that the attacker sandwiches the transaction on the wom/mWom Wombat pool, fuzz the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from), and assert after every call that the mint leg must remain usable regardless of allowance residue.
