# Q1242: mWOM.deposit - no whenNotPaused on the internal _convert guard ordering

## Question
In wombat/mWOM.sol, _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Does `deposit(uint256 _amount)` let an unprivileged caller exploit that under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, so that `IERC20(this).totalSupply()` diverges from `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked) under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, asserting on every row that the pause state governing a value transfer and the mint it backs must be evaluated once.
