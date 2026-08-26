# Q0102: mWOM.convert - no whenNotPaused on the internal _convert guard ordering

## Question
wombat/mWOM.sol: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Under rewardRatio has been switched on and the contract holds a freshly funded MGP balance, is there an unprivileged sequence of `convert(uint256 _amount)` that leaves `rewardRatio` unreconciled with `DENOMINATOR`, violates the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, and the block relative to any pending convertAllWom) under rewardRatio has been switched on and the contract holds a freshly funded MGP balance, asserting on every row that the pause state governing a value transfer and the mint it backs must be evaluated once.
