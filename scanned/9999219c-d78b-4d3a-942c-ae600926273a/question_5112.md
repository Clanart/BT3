# Q5112: WombatPoolHelper.deposit - no reentrancy guard anywhere on the helper

## Question
Consider wombat/WombatPoolHelper.sol, where none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Assuming the attacker has moved the wom/mWom Wombat pool immediately before calling, can an unprivileged attacker turn this into a divergence between `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` via `deposit(uint256 _amount, uint256 _minimumLiquidity)`, breaking the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _minimumLiquidity, forwarded verbatim into the Wombat pool) under the attacker has moved the wom/mWom Wombat pool immediately before calling, asserting on every row that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard.
