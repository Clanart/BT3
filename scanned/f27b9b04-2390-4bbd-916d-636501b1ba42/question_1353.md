# Q1353: mWOM.incentiveDeposit - incentiveDeposit safeApprove without reset

## Question
Note that in wombat/mWOM.sol, incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, bool _stake)` under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit and force `IERC20(this).totalSupply()` apart from `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, breaking the invariant that an approval on a repeated path must be idempotent for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit safeApprove without reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, have the attacker run `incentiveDeposit(uint256 _amount, bool _stake)`, then assert the victim's claimable value and the `IERC20(this).totalSupply()` versus `IERC20(wom).balanceOf(wombatStaking) + veWom backing` relation are unchanged by the attacker's transaction.
