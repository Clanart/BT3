# Q4902: mWOM.incentiveDeposit - no whenNotPaused on the internal _convert guard ordering

## Question
Consider wombat/mWOM.sol, where _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Assuming the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, can an unprivileged attacker turn this into a divergence between `rewardRatio` and `DENOMINATOR` via `incentiveDeposit(uint256 _amount, bool _stake)`, breaking the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero) under the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, asserting on every row that the pause state governing a value transfer and the mint it backs must be evaluated once.
