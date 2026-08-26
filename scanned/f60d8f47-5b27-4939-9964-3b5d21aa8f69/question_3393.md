# Q3393: mWOM.deposit - no whenNotPaused on the internal _convert guard ordering

## Question
wombat/mWOM.sol - _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Can an unprivileged attacker controlling _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked, under the veWOM mint returns less than the WOM supplied because of the lockDays curve, exploit this through `deposit(uint256 _amount)` to break the reconciliation between `IERC20(wom).balanceOf(address(this))` and `totalConverted` and the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the veWOM mint returns less than the WOM supplied because of the lockDays curve, have the attacker run `deposit(uint256 _amount)`, then assert the victim's claimable value and the `IERC20(wom).balanceOf(address(this))` versus `totalConverted` relation are unchanged by the attacker's transaction.
