# Q4419: mWOM.convertAndStake - no whenNotPaused on the internal _convert guard ordering

## Question
In wombat/mWOM.sol, _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Does `convertAndStake(uint256 _amount)` let an unprivileged caller exploit that under the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, so that `IERC20(this).totalSupply()` diverges from `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, have the attacker run `convertAndStake(uint256 _amount)`, then assert the victim's claimable value and the `IERC20(this).totalSupply()` versus `IERC20(wom).balanceOf(wombatStaking) + veWom backing` relation are unchanged by the attacker's transaction.
