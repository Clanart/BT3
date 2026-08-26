# Q1972: mWOM.incentiveDeposit - incentiveDeposit safeApprove without reset

## Question
wombat/mWOM.sol: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. With _amount with no cap, and _stake, while rewardRatio is non-zero under attacker control and an owner funding transfer of MGP is sitting in the mempool, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, bool _stake)` so that `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM` no longer reconcile, violating the invariant that an approval on a repeated path must be idempotent and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit safeApprove without reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), vlMGPAmount) with no prior zeroing, so residue from a lockFor that did not consume the full allowance permanently disables the incentive path. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, bool _stake)`: constrain the setup so that an owner funding transfer of MGP is sitting in the mempool, fuzz the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero), and assert after every call that an approval on a repeated path must be idempotent.
