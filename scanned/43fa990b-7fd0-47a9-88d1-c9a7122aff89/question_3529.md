# Q3529: mWOM.incentiveDeposit - no whenNotPaused on the internal _convert guard ordering

## Question
In wombat/mWOM.sol, _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Does `incentiveDeposit(uint256 _amount, bool _stake)` let an unprivileged caller exploit that under the veWOM mint returns less than the WOM supplied because of the lockDays curve, so that `totalConverted` diverges from `totalAccumulated`, the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the veWOM mint returns less than the WOM supplied because of the lockDays curve, call `incentiveDeposit(uint256 _amount, bool _stake)`, and assert `totalConverted` equals `totalAccumulated` and that no account can withdraw more than it put in.
