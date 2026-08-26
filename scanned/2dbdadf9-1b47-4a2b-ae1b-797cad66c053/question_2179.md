# Q2179: mWOM.convert - no whenNotPaused on the internal _convert guard ordering

## Question
wombat/mWOM.sol - _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Can an unprivileged attacker controlling _amount, and the block relative to any pending convertAllWom, under wombatStaking is holding WOM from an earlier deposit that has not been locked, exploit this through `convert(uint256 _amount)` to break the reconciliation between `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up wombatStaking is holding WOM from an earlier deposit that has not been locked, snapshot `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, run the attacker's `convert(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
