# Q4957: mWOM.convert - no whenNotPaused on the internal _convert guard ordering

## Question
Note that in wombat/mWOM.sol, _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Can an attacker holding only tokens bought on market reach it via `convert(uint256 _amount)` under the attacker repeats the call across several addresses in the same block and force `_amount minted as mWOM` apart from `mintedVeWomAmount returned by IWombatStaking.convertWOM`, breaking the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: the attacker repeats the call across several addresses in the same block.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, and the block relative to any pending convertAllWom) under the attacker repeats the call across several addresses in the same block, asserting on every row that the pause state governing a value transfer and the mint it backs must be evaluated once.
