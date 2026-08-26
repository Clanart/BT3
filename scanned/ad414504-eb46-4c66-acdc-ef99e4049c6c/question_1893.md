# Q1893: WomUp.migrate - migrate approves without resetting the allowance

## Question
wombat/WomUp.sol: migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. With _amount, _claim and which allowlisted helper receives the position under attacker control and the target helper leaves a non-zero allowance after depositFor, can an unprivileged caller sequence `migrate(uint256 _amount, bool _claim, address _targetHelper)` so that `rewards[account]` and `IERC20(mgp).balanceOf(address(this))` no longer reconcile, violating the invariant that an approval on a repeated path must be idempotent and must be cleared after use and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate approves without resetting the allowance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: an approval on a repeated path must be idempotent and must be cleared after use; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `migrate(uint256 _amount, bool _claim, address _targetHelper)`: constrain the setup so that the target helper leaves a non-zero allowance after depositFor, fuzz the attacker inputs (_amount, _claim and which allowlisted helper receives the position), and assert after every call that an approval on a repeated path must be idempotent and must be cleared after use.
