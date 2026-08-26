# Q2819: WomUp.migrate - migrate approves without resetting the allowance

## Question
wombat/WomUp.sol: migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Under the attacker calls getReward immediately after a large stake by another user, is there an unprivileged sequence of `migrate(uint256 _amount, bool _claim, address _targetHelper)` that leaves `_balances[account]` unreconciled with `_totalSupply`, violates the invariant that an approval on a repeated path must be idempotent and must be cleared after use, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate approves without resetting the allowance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Precondition: the attacker calls getReward immediately after a large stake by another user.
- Invariant to test: an approval on a repeated path must be idempotent and must be cleared after use; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker calls getReward immediately after a large stake by another user, snapshot `_balances[account]` and `_totalSupply`, run the attacker's `migrate(uint256 _amount, bool _claim, address _targetHelper)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
