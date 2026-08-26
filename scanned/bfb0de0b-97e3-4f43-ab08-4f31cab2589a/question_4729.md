# Q4729: mWOM.convertAndStake - no whenNotPaused on the internal _convert guard ordering

## Question
In wombat/mWOM.sol, _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Starting from a state where the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, can an unprivileged EOA use `convertAndStake(uint256 _amount)` to leave `_amount minted as mWOM` inconsistent with `mintedVeWomAmount returned by IWombatStaking.convertWOM`, violating the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, call `convertAndStake(uint256 _amount)`, and assert `_amount minted as mWOM` equals `mintedVeWomAmount returned by IWombatStaking.convertWOM` and that no account can withdraw more than it put in.
