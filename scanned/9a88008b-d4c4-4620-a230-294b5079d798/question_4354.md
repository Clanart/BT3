# Q4354: mWOM.convert - no whenNotPaused on the internal _convert guard ordering

## Question
In wombat/mWOM.sol, _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Starting from a state where the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, can an unprivileged EOA use `convert(uint256 _amount)` to leave `totalConverted` inconsistent with `totalAccumulated`, violating the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, then assert `totalConverted` and `totalAccumulated` end identical in both runs.
