# Q2732: mWOM.convert - no whenNotPaused on the internal _convert guard ordering

## Question
In wombat/mWOM.sol, _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Does `convert(uint256 _amount)` let an unprivileged caller exploit that under the attacker calls convertAllWom on WombatStaking in the same transaction, so that `_amount minted as mWOM` diverges from `mintedVeWomAmount returned by IWombatStaking.convertWOM`, the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls convertAllWom on WombatStaking in the same transaction, call `convert(uint256 _amount)`, and assert `_amount minted as mWOM` equals `mintedVeWomAmount returned by IWombatStaking.convertWOM` and that no account can withdraw more than it put in.
