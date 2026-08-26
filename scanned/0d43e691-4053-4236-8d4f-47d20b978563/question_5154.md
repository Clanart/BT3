# Q5154: WombatPoolHelper.depositLP - no reentrancy guard anywhere on the helper

## Question
In wombat/WombatPoolHelper.sol, none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Does `depositLP(uint256 _lpAmount)` let an unprivileged caller exploit that under the attacker has moved the wom/mWom Wombat pool immediately before calling, so that `this.balance(msg.sender)` diverges from `lockedAmount[msg.sender]`, the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount and the LP tokens pulled from the caller) under the attacker has moved the wom/mWom Wombat pool immediately before calling, asserting on every row that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard.
