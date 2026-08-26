# Q3865: mWOM.incentiveDeposit - incentiveDeposit safeApprove without reset

## Question
Note that in wombat/mWOM.sol, incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, bool _stake)` under helper is set to a SimplePoolHelper and the attacker uses convertAndStake and force `totalConverted` apart from `totalAccumulated`, breaking the invariant that an approval on a repeated path must be idempotent for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit safeApprove without reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, bool _stake)`: constrain the setup so that helper is set to a SimplePoolHelper and the attacker uses convertAndStake, fuzz the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero), and assert after every call that an approval on a repeated path must be idempotent.
