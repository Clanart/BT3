# Q2215: WomUp.migrate - migrate approves without resetting the allowance

## Question
In wombat/WomUp.sol, migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Starting from a state where the attacker migrates and withdraws inside one transaction, can an unprivileged EOA use `migrate(uint256 _amount, bool _claim, address _targetHelper)` to leave `lastUpdateTime` inconsistent with `periodFinish`, violating the invariant that an approval on a repeated path must be idempotent and must be cleared after use and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate approves without resetting the allowance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Precondition: the attacker migrates and withdraws inside one transaction.
- Invariant to test: an approval on a repeated path must be idempotent and must be cleared after use; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker migrates and withdraws inside one transaction, then assert `lastUpdateTime` and `periodFinish` end identical in both runs.
