# Q5191: AnkrBNBPoolHelper.depositLP - no reentrancy guard anywhere on the helper

## Question
Consider wombat/AnkrBNBPoolHelper.sol, where none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Assuming the attacker deposits and withdraws through the helper inside one transaction, can an unprivileged attacker turn this into a divergence between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` via `depositLP(uint256 _lpAmount)`, breaking the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `depositLP(uint256 _lpAmount)`: constrain the setup so that the attacker deposits and withdraws through the helper inside one transaction, fuzz the attacker inputs (_lpAmount), and assert after every call that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard.
