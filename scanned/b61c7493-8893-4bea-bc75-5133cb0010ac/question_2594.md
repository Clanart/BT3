# Q2594: WombatPoolHelper.harvest - no reentrancy guard anywhere on the helper

## Question
In wombat/WombatPoolHelper.sol, none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Can an unprivileged attacker reach this through `harvest()` while the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, and drive `pid cached at construction` out of agreement with `pools[lpToken].pid in WombatStaking` - breaking the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `harvest()` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the pool's rewards are harvested and fee-split
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `harvest()`: constrain the setup so that the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, fuzz the attacker inputs (the exact block at which the pool's rewards are harvested and fee-split), and assert after every call that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard.
