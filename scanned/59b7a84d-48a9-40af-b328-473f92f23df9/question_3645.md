# Q3645: AnkrBNBPoolHelper.deposit - no reentrancy guard anywhere on the helper

## Question
wombat/AnkrBNBPoolHelper.sol: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, is there an unprivileged sequence of `deposit(uint256 _amount, uint256 _minimumLiquidity)` that leaves `_minimumLiquidity supplied by the caller` unreconciled with `the LP actually minted by the Wombat pool`, violates the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _minimumLiquidity) under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, asserting on every row that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard.
