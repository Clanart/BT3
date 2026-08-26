# Q0737: WomUp.migrate - migrate approves without resetting the allowance

## Question
In wombat/WomUp.sol, migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Does `migrate(uint256 _amount, bool _claim, address _targetHelper)` let an unprivileged caller exploit that under the attacker funds the stake with a flash loan of WOM repaid in the same transaction, so that `_balances[account]` diverges from `_totalSupply`, the invariant that an approval on a repeated path must be idempotent and must be cleared after use is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate approves without resetting the allowance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Precondition: the attacker funds the stake with a flash loan of WOM repaid in the same transaction.
- Invariant to test: an approval on a repeated path must be idempotent and must be cleared after use; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker funds the stake with a flash loan of WOM repaid in the same transaction, have the attacker run `migrate(uint256 _amount, bool _claim, address _targetHelper)`, then assert the victim's claimable value and the `_balances[account]` versus `_totalSupply` relation are unchanged by the attacker's transaction.
