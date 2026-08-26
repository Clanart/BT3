# Q4510: mWOM.deposit - no whenNotPaused on the internal _convert guard ordering

## Question
wombat/mWOM.sol: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. With _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked under attacker control and the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, can an unprivileged caller sequence `deposit(uint256 _amount)` so that `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM` no longer reconcile, violating the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, have the attacker run `deposit(uint256 _amount)`, then assert the victim's claimable value and the `_amount minted as mWOM` versus `mintedVeWomAmount returned by IWombatStaking.convertWOM` relation are unchanged by the attacker's transaction.
