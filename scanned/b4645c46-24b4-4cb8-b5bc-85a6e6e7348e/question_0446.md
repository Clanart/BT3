# Q0446: WombatPoolHelperV2.depositLP - no reentrancy guard anywhere on the helper

## Question
Consider wombat/WombatPoolHelperV2.sol, where none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Assuming the pool's deposit token is wBNB and the caller arrived through depositNative, can an unprivileged attacker turn this into a divergence between `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` via `depositLP(uint256 _lpAmount)`, breaking the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool's deposit token is wBNB and the caller arrived through depositNative, call `depositLP(uint256 _lpAmount)`, and assert `_liquidity burned via burnReceiptToken` equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and that no account can withdraw more than it put in.
