# Q1551: WomUp.migrate - migrate approves without resetting the allowance

## Question
In wombat/WomUp.sol, migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Starting from a state where the reward period has just ended so periodFinish is behind block.timestamp, can an unprivileged EOA use `migrate(uint256 _amount, bool _claim, address _targetHelper)` to leave `rewardPerTokenStored` inconsistent with `userRewardPerTokenPaid[account]`, violating the invariant that an approval on a repeated path must be idempotent and must be cleared after use and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WomUp.sol -> `migrate(uint256 _amount, bool _claim, address _targetHelper)` (mechanism: migrate approves without resetting the allowance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `migrate(uint256 _amount, bool _claim, address _targetHelper)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _claim and which allowlisted helper receives the position
- Exploit idea: migrate() calls IERC20(mWom).safeApprove(_targetHelper, _amount) with no prior zeroing and never approves zero afterwards, so a helper that under-consumes leaves residue that permanently blocks every later migration. Precondition: the reward period has just ended so periodFinish is behind block.timestamp.
- Invariant to test: an approval on a repeated path must be idempotent and must be cleared after use; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `migrate(uint256 _amount, bool _claim, address _targetHelper)`: constrain the setup so that the reward period has just ended so periodFinish is behind block.timestamp, fuzz the attacker inputs (_amount, _claim and which allowlisted helper receives the position), and assert after every call that an approval on a repeated path must be idempotent and must be cleared after use.
