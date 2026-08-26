# Q3104: mWOM.incentiveDeposit - no whenNotPaused on the internal _convert guard ordering

## Question
wombat/mWOM.sol - _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Can an unprivileged attacker controlling _amount with no cap, and _stake, while rewardRatio is non-zero, under the attacker calls convertAllWom on WombatStaking in the same transaction, exploit this through `incentiveDeposit(uint256 _amount, bool _stake)` to break the reconciliation between `IERC20(wom).balanceOf(address(this))` and `totalConverted` and the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, bool _stake)`: constrain the setup so that the attacker calls convertAllWom on WombatStaking in the same transaction, fuzz the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero), and assert after every call that the pause state governing a value transfer and the mint it backs must be evaluated once.
