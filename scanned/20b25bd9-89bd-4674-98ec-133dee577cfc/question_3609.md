# Q3609: mWOM.convert - no whenNotPaused on the internal _convert guard ordering

## Question
Consider wombat/mWOM.sol, where _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Assuming helper is set to a SimplePoolHelper and the attacker uses convertAndStake, can an unprivileged attacker turn this into a divergence between `rewardRatio` and `DENOMINATOR` via `convert(uint256 _amount)`, breaking the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish helper is set to a SimplePoolHelper and the attacker uses convertAndStake, have the attacker run `convert(uint256 _amount)`, then assert the victim's claimable value and the `rewardRatio` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
