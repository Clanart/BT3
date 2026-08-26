# Q2547: mWOM.incentiveDeposit - incentiveDeposit safeApprove without reset

## Question
In wombat/mWOM.sol, incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount, bool _stake)` while wombatStaking is holding WOM from an earlier deposit that has not been locked, and drive `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` out of agreement with `IERC20(mgp).balanceOf(address(this))` - breaking the invariant that an approval on a repeated path must be idempotent - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit safeApprove without reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, bool _stake)` sequence atomically under wombatStaking is holding WOM from an earlier deposit that has not been locked, asserting at the end that `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` still equals `IERC20(mgp).balanceOf(address(this))` and the PoC's balance delta is non-positive.
