# Q0785: SmartWomConvert.smartConvert - safeApprove without reset on the mWOM mint leg

## Question
In wombat/SmartWomConvert.sol, _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Starting from a state where the attacker has pushed mWom below buybackThreshold against wom in the same transaction, can an unprivileged EOA use `smartConvert(uint256 _amountIn, uint256 _mode)` to leave `_convertRatio` inconsistent with `DENOMINATOR`, violating the invariant that the mint leg must remain usable regardless of allowance residue and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: safeApprove without reset on the mWOM mint leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Precondition: the attacker has pushed mWom below buybackThreshold against wom in the same transaction.
- Invariant to test: the mint leg must remain usable regardless of allowance residue; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `smartConvert(uint256 _amountIn, uint256 _mode)`: constrain the setup so that the attacker has pushed mWom below buybackThreshold against wom in the same transaction, fuzz the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from), and assert after every call that the mint leg must remain usable regardless of allowance residue.
