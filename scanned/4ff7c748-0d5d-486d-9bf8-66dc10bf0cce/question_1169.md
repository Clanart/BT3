# Q1169: WomUp.migrate - migrate approves without resetting the allowance

## Question
In wombat/WomUp.sol, migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Does `migrate(uint256 _amount, bool _claim, address _targetHelper)` let an unprivileged caller exploit that under _totalSupply exceeds the mWOM balance the contract actually holds, so that `_totalSupply` diverges from `IERC20(mWom).balanceOf(address(this))`, the invariant that an approval on a repeated path must be idempotent and must be cleared after use is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate approves without resetting the allowance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Precondition: _totalSupply exceeds the mWOM balance the contract actually holds.
- Invariant to test: an approval on a repeated path must be idempotent and must be cleared after use; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `migrate(uint256 _amount, bool _claim, address _targetHelper)` sequence atomically under _totalSupply exceeds the mWOM balance the contract actually holds, asserting at the end that `_totalSupply` still equals `IERC20(mWom).balanceOf(address(this))` and the PoC's balance delta is non-positive.
