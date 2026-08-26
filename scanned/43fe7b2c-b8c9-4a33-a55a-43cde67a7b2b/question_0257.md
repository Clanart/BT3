# Q0257: mWOM.convertAndStake - no whenNotPaused on the internal _convert guard ordering

## Question
In wombat/mWOM.sol, _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Starting from a state where rewardRatio has been switched on and the contract holds a freshly funded MGP balance, can an unprivileged EOA use `convertAndStake(uint256 _amount)` to leave `IERC20(wom).balanceOf(address(this))` inconsistent with `totalConverted`, violating the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up rewardRatio has been switched on and the contract holds a freshly funded MGP balance, snapshot `IERC20(wom).balanceOf(address(this))` and `totalConverted`, run the attacker's `convertAndStake(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
