# Q4237: mWOM.incentiveDeposit - incentiveDeposit safeApprove without reset

## Question
Consider wombat/mWOM.sol, where incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Assuming helper is unset so convertAndStake reverts and only the plain mint path is reachable, can an unprivileged attacker turn this into a divergence between `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing` via `incentiveDeposit(uint256 _amount, bool _stake)`, breaking the invariant that an approval on a repeated path must be idempotent and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit safeApprove without reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Precondition: helper is unset so convertAndStake reverts and only the plain mint path is reachable.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, bool _stake)` sequence atomically under helper is unset so convertAndStake reverts and only the plain mint path is reachable, asserting at the end that `IERC20(this).totalSupply()` still equals `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and the PoC's balance delta is non-positive.
