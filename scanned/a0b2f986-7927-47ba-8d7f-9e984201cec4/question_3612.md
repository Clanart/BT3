# Q3612: WombatPoolHelperV2.harvest - no reentrancy guard anywhere on the helper

## Question
In wombat/WombatPoolHelperV2.sol, none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Starting from a state where a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged EOA use `harvest()` to leave `this.balance(msg.sender)` inconsistent with `lockedAmount[msg.sender]`, violating the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `harvest()` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a residual stakingToken balance from an earlier rounding sits on the helper, snapshot `this.balance(msg.sender)` and `lockedAmount[msg.sender]`, run the attacker's `harvest()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
