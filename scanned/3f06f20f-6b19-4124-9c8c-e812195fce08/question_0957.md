# Q0957: ArbWomUp3.incentiveDeposit - safeApprove without reset on the vlMGP reward leg

## Question
Consider wombat/ArbWomUp3.sol, where incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), rewardToSend) with no prior zeroing, so residue from a lockFor that under-consumes permanently disables the incentive for every participant. Assuming the caller sets _mode to a value other than 1 or 2, can an unprivileged attacker turn this into a divergence between `rewardToSend` and `IERC20(mgp).balanceOf(address(this))` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that an approval on a repeated path must be idempotent and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: safeApprove without reset on the vlMGP reward leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), rewardToSend) with no prior zeroing, so residue from a lockFor that under-consumes permanently disables the incentive for every participant. Precondition: the caller sets _mode to a value other than 1 or 2.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `rewardToSend` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the caller sets _mode to a value other than 1 or 2, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, then assert the victim's claimable value and the `rewardToSend` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
