# Q0598: mWOM.incentiveDeposit - incentiveDeposit safeApprove without reset

## Question
In wombat/mWOM.sol, incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Starting from a state where rewardRatio has been switched on and the contract holds a freshly funded MGP balance, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, bool _stake)` to leave `totalConverted` inconsistent with `totalAccumulated`, violating the invariant that an approval on a repeated path must be idempotent and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit safeApprove without reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish rewardRatio has been switched on and the contract holds a freshly funded MGP balance, have the attacker run `incentiveDeposit(uint256 _amount, bool _stake)`, then assert the victim's claimable value and the `totalConverted` versus `totalAccumulated` relation are unchanged by the attacker's transaction.
