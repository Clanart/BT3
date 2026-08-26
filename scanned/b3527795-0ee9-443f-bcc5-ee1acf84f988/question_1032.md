# Q1032: mWOM.convertAndStake - no whenNotPaused on the internal _convert guard ordering

## Question
Consider wombat/mWOM.sol, where _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Assuming rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, can an unprivileged attacker turn this into a divergence between `totalConverted` and `totalAccumulated` via `convertAndStake(uint256 _amount)`, breaking the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and the helper routing that stakes the freshly minted mWOM) under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, asserting on every row that the pause state governing a value transfer and the mint it backs must be evaluated once.
