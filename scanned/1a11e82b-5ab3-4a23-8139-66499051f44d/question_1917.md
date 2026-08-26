# Q1917: ArbWomUp3.incentiveDeposit - safeApprove without reset on the vlMGP reward leg

## Question
Consider wombat/ArbWomUp3.sol, where incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), rewardToSend) with no prior zeroing, so residue from a lockFor that under-consumes permanently disables the incentive for every participant. Assuming the caller sandwiches the wom/mWom Wombat pool around the transaction, can an unprivileged attacker turn this into a divergence between `IERC20(mWom).balanceOf(address(this))` and `the amount locked for _account in mode two` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that an approval on a repeated path must be idempotent and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: safeApprove without reset on the vlMGP reward leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), rewardToSend) with no prior zeroing, so residue from a lockFor that under-consumes permanently disables the incentive for every participant. Precondition: the caller sandwiches the wom/mWom Wombat pool around the transaction.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `IERC20(mWom).balanceOf(address(this))` must stay reconciled with `the amount locked for _account in mode two`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`: constrain the setup so that the caller sandwiches the wom/mWom Wombat pool around the transaction, fuzz the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer), and assert after every call that an approval on a repeated path must be idempotent.
