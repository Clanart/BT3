# Q3801: mWOM.deposit - no whenNotPaused on the internal _convert guard ordering

## Question
wombat/mWOM.sol - _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Can an unprivileged attacker controlling _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked, under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, exploit this through `deposit(uint256 _amount)` to break the reconciliation between `totalConverted` and `totalAccumulated` and the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange helper is set to a SimplePoolHelper and the attacker uses convertAndStake, call `deposit(uint256 _amount)`, and assert `totalConverted` equals `totalAccumulated` and that no account can withdraw more than it put in.
