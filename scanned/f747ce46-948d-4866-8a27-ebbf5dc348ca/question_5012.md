# Q5012: mWOM.convertAndStake - no whenNotPaused on the internal _convert guard ordering

## Question
Note that in wombat/mWOM.sol, _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Can an attacker holding only tokens bought on market reach it via `convertAndStake(uint256 _amount)` under the attacker repeats the call across several addresses in the same block and force `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` apart from `IERC20(mgp).balanceOf(address(this))`, breaking the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: the attacker repeats the call across several addresses in the same block.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the attacker repeats the call across several addresses in the same block, have the attacker run `convertAndStake(uint256 _amount)`, then assert the victim's claimable value and the `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
