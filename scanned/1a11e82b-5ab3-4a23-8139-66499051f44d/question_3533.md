# Q3533: AnkrBNBPoolHelper.harvest - no reentrancy guard anywhere on the helper

## Question
In wombat/AnkrBNBPoolHelper.sol, none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Starting from a state where a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged EOA use `harvest()` to leave `_liquidity burned via burnReceiptToken` inconsistent with `the deposit-token balance delta paid out by WombatStaking.withdraw`, violating the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `harvest()` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish a residual stakingToken balance from an earlier rounding sits on the helper, have the attacker run `harvest()`, then assert the victim's claimable value and the `_liquidity burned via burnReceiptToken` versus `the deposit-token balance delta paid out by WombatStaking.withdraw` relation are unchanged by the attacker's transaction.
