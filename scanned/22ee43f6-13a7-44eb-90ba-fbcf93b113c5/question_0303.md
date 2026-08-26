# Q0303: WomUp.migrate - migrate approves without resetting the allowance

## Question
In wombat/WomUp.sol, migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Starting from a state where the attacker is the only staker for a single block, can an unprivileged EOA use `migrate(uint256 _amount, bool _claim, address _targetHelper)` to leave `rewardRate * duration` inconsistent with `IERC20(mgp).balanceOf(address(this))`, violating the invariant that an approval on a repeated path must be idempotent and must be cleared after use and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate approves without resetting the allowance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Precondition: the attacker is the only staker for a single block.
- Invariant to test: an approval on a repeated path must be idempotent and must be cleared after use; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `migrate(uint256 _amount, bool _claim, address _targetHelper)` sequence atomically under the attacker is the only staker for a single block, asserting at the end that `rewardRate * duration` still equals `IERC20(mgp).balanceOf(address(this))` and the PoC's balance delta is non-positive.
