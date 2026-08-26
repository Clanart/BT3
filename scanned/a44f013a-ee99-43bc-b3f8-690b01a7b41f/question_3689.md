# Q3689: mWOM.convertAndStake - no whenNotPaused on the internal _convert guard ordering

## Question
In wombat/mWOM.sol, _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Starting from a state where helper is set to a SimplePoolHelper and the attacker uses convertAndStake, can an unprivileged EOA use `convertAndStake(uint256 _amount)` to leave `IERC20(wom).balanceOf(address(this))` inconsistent with `totalConverted`, violating the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange helper is set to a SimplePoolHelper and the attacker uses convertAndStake, call `convertAndStake(uint256 _amount)`, and assert `IERC20(wom).balanceOf(address(this))` equals `totalConverted` and that no account can withdraw more than it put in.
