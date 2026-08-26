# Q3034: mWOM.incentiveDeposit - incentiveDeposit safeApprove without reset

## Question
wombat/mWOM.sol: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Under the attacker calls convertAllWom on WombatStaking in the same transaction, is there an unprivileged sequence of `incentiveDeposit(uint256 _amount, bool _stake)` that leaves `rewardRatio` unreconciled with `DENOMINATOR`, violates the invariant that an approval on a repeated path must be idempotent, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit safeApprove without reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, bool _stake)`: constrain the setup so that the attacker calls convertAllWom on WombatStaking in the same transaction, fuzz the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero), and assert after every call that an approval on a repeated path must be idempotent.
