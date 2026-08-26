# Q1590: mWOM.convert - no whenNotPaused on the internal _convert guard ordering

## Question
wombat/mWOM.sol: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Under an owner funding transfer of MGP is sitting in the mempool, is there an unprivileged sequence of `convert(uint256 _amount)` that leaves `totalConverted` unreconciled with `totalAccumulated`, violates the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish an owner funding transfer of MGP is sitting in the mempool, have the attacker run `convert(uint256 _amount)`, then assert the victim's claimable value and the `totalConverted` versus `totalAccumulated` relation are unchanged by the attacker's transaction.
