# Q1731: ArbWomUp3.incentiveDeposit - an unrecognised mode silently takes the plain transfer branch

## Question
wombat/ArbWomUp3.sol - _deposit() treats mode 1 as a SmartWomConvert deposit, mode 2 as the swap-and-lock path and anything else as a plain mWOM transfer, so an unexpected mode value falls through to the least restrictive settlement while the reward was priced for a different one. Can an unprivileged attacker controlling _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer, under the caller sets _convertRatio to zero so the whole leg is swapped, exploit this through `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` to break the reconciliation between `rewardToSend` and `IERC20(mgp).balanceOf(address(this))` and the invariant that an unrecognised routing mode must revert rather than settle on the least restrictive branch, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: an unrecognised mode silently takes the plain transfer branch)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() treats mode 1 as a SmartWomConvert deposit, mode 2 as the swap-and-lock path and anything else as a plain mWOM transfer, so an unexpected mode value falls through to the least restrictive settlement while the reward was priced for a different one. Precondition: the caller sets _convertRatio to zero so the whole leg is swapped.
- Invariant to test: an unrecognised routing mode must revert rather than settle on the least restrictive branch; concretely, `rewardToSend` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets _convertRatio to zero so the whole leg is swapped, call `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, and assert `rewardToSend` equals `IERC20(mgp).balanceOf(address(this))` and that no account can withdraw more than it put in.
