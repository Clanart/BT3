# Q1631: ArbWomUp3.incentiveDeposit - safeApprove without reset on the vlMGP reward leg

## Question
wombat/ArbWomUp3.sol - incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), rewardToSend) with no prior zeroing, so residue from a lockFor that under-consumes permanently disables the incentive for every participant. Can an unprivileged attacker controlling _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer, under the caller sets _convertRatio to zero so the whole leg is swapped, exploit this through `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` to break the reconciliation between `_convertRatio supplied by the caller` and `the buyback leg inside SmartWomConvert` and the invariant that an approval on a repeated path must be idempotent, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: safeApprove without reset on the vlMGP reward leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), rewardToSend) with no prior zeroing, so residue from a lockFor that under-consumes permanently disables the incentive for every participant. Precondition: the caller sets _convertRatio to zero so the whole leg is swapped.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the buyback leg inside SmartWomConvert`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`: constrain the setup so that the caller sets _convertRatio to zero so the whole leg is swapped, fuzz the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer), and assert after every call that an approval on a repeated path must be idempotent.
