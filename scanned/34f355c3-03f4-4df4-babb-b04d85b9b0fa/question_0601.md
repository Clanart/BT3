# Q0601: WombatPoolHelperV2.depositNative - no reentrancy guard anywhere on the helper

## Question
Note that in wombat/WombatPoolHelperV2.sol, none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Can an attacker holding only tokens bought on market reach it via `depositNative(uint256 _minimumLiquidity)` under the pool's deposit token is wBNB and the caller arrived through depositNative and force `this.balance(msg.sender)` apart from `lockedAmount[msg.sender]`, breaking the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `depositNative(uint256 _minimumLiquidity)`: constrain the setup so that the pool's deposit token is wBNB and the caller arrived through depositNative, fuzz the attacker inputs (msg.value and _minimumLiquidity), and assert after every call that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard.
