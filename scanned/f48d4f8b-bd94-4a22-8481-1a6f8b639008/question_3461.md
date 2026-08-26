# Q3461: mWOM.incentiveDeposit - incentiveDeposit safeApprove without reset

## Question
Consider wombat/mWOM.sol, where incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Assuming the veWOM mint returns less than the WOM supplied because of the lockDays curve, can an unprivileged attacker turn this into a divergence between `IERC20(wom).balanceOf(address(this))` and `totalConverted` via `incentiveDeposit(uint256 _amount, bool _stake)`, breaking the invariant that an approval on a repeated path must be idempotent and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit safeApprove without reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the veWOM mint returns less than the WOM supplied because of the lockDays curve, then assert `IERC20(wom).balanceOf(address(this))` and `totalConverted` end identical in both runs.
