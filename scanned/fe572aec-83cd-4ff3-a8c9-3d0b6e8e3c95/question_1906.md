# Q1906: WombatPoolHelperV2.deposit - no reentrancy guard anywhere on the helper

## Question
wombat/WombatPoolHelperV2.sol - none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Can an unprivileged attacker controlling _amount and _minimumLiquidity, under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, exploit this through `deposit(uint256 _amount, uint256 _minimumLiquidity)` to break the reconciliation between `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` and the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, snapshot `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw`, run the attacker's `deposit(uint256 _amount, uint256 _minimumLiquidity)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
