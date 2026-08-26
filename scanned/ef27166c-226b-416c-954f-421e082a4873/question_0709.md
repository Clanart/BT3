# Q0709: ArbWomUp3.incentiveDeposit - an unrecognised mode silently takes the plain transfer branch

## Question
Note that in wombat/ArbWomUp3.sol, _deposit() treats mode 1 as a SmartWomConvert deposit, mode 2 as the swap-and-lock path and anything else as a plain mWOM transfer, so an unexpected mode value falls through to the least restrictive settlement while the reward was priced for a different one. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under the caller sets _mode to 2 so the doubling applies and force `IERC20(mWom).balanceOf(address(this))` apart from `the amount locked for _account in mode two`, breaking the invariant that an unrecognised routing mode must revert rather than settle on the least restrictive branch for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: an unrecognised mode silently takes the plain transfer branch)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() treats mode 1 as a SmartWomConvert deposit, mode 2 as the swap-and-lock path and anything else as a plain mWOM transfer, so an unexpected mode value falls through to the least restrictive settlement while the reward was priced for a different one. Precondition: the caller sets _mode to 2 so the doubling applies.
- Invariant to test: an unrecognised routing mode must revert rather than settle on the least restrictive branch; concretely, `IERC20(mWom).balanceOf(address(this))` must stay reconciled with `the amount locked for _account in mode two`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller sets _mode to 2 so the doubling applies, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, then assert the victim's claimable value and the `IERC20(mWom).balanceOf(address(this))` versus `the amount locked for _account in mode two` relation are unchanged by the attacker's transaction.
