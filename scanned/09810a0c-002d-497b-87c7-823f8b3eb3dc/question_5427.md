# Q5427: WombatPoolHelper.depositNative - depositNative wraps msg.value and approves the exact amount

## Question
In wombat/WombatPoolHelper.sol, depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Does `depositNative(uint256 _minimumLiquidity)` let an unprivileged caller exploit that under the attacker deposits and withdraws through the helper inside one transaction, so that `this.balance(msg.sender)` diverges from `lockedAmount[msg.sender]`, the invariant that native value wrapped for a deposit must always end the transaction attributed to a depositor is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: depositNative wraps msg.value and approves the exact amount)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: native value wrapped for a deposit must always end the transaction attributed to a depositor; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker deposits and withdraws through the helper inside one transaction, have the attacker run `depositNative(uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `this.balance(msg.sender)` versus `lockedAmount[msg.sender]` relation are unchanged by the attacker's transaction.
