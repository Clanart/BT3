# Q1880: mWOM.deposit - no whenNotPaused on the internal _convert guard ordering

## Question
In wombat/mWOM.sol, _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Starting from a state where an owner funding transfer of MGP is sitting in the mempool, can an unprivileged EOA use `deposit(uint256 _amount)` to leave `_amount minted as mWOM` inconsistent with `mintedVeWomAmount returned by IWombatStaking.convertWOM`, violating the invariant that the pause state governing a value transfer and the mint it backs must be evaluated once and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: no whenNotPaused on the internal _convert guard ordering)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert carries whenNotPaused and nonReentrant while the external wrappers carry whenNotPaused too, so the pause state is evaluated twice around an external transfer and the WOM leg can land in a different pause state than the mint leg. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: the pause state governing a value transfer and the mint it backs must be evaluated once; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange an owner funding transfer of MGP is sitting in the mempool, call `deposit(uint256 _amount)`, and assert `_amount minted as mWOM` equals `mintedVeWomAmount returned by IWombatStaking.convertWOM` and that no account can withdraw more than it put in.
