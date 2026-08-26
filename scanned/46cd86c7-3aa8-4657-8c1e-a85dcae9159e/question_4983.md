# Q4983: AnkrBNBPoolHelper.depositNative - depositNative wraps msg.value and approves the exact amount

## Question
wombat/AnkrBNBPoolHelper.sol: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. With msg.value and _minimumLiquidity under attacker control and the attacker has moved the wom/mWom Wombat pool immediately before calling, can an unprivileged caller sequence `depositNative(uint256 _minimumLiquidity)` so that `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` no longer reconcile, violating the invariant that native value wrapped for a deposit must always end the transaction attributed to a depositor and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: depositNative wraps msg.value and approves the exact amount)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: native value wrapped for a deposit must always end the transaction attributed to a depositor; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has moved the wom/mWom Wombat pool immediately before calling, call `depositNative(uint256 _minimumLiquidity)`, and assert `_liquidity burned via burnReceiptToken` equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and that no account can withdraw more than it put in.
