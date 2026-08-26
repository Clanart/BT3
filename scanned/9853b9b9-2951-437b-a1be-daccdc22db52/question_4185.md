# Q4185: WombatPoolHelper.withdraw - no reentrancy guard anywhere on the helper

## Question
Note that in wombat/WombatPoolHelper.sol, none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Can an attacker holding only tokens bought on market reach it via `withdraw(uint256 _liquidity, uint256 _minAmount)` under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes and force `_liquidity burned via burnReceiptToken` apart from `the deposit-token balance delta paid out by WombatStaking.withdraw`, breaking the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, snapshot `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw`, run the attacker's `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
