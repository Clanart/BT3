# Q2228: WombatPoolHelperV2.depositNative - depositNative wraps msg.value and approves the exact amount

## Question
wombat/WombatPoolHelperV2.sol: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, is there an unprivileged sequence of `depositNative(uint256 _minimumLiquidity)` that leaves `this.balance(msg.sender)` unreconciled with `lockedAmount[msg.sender]`, violates the invariant that native value wrapped for a deposit must always end the transaction attributed to a depositor, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: depositNative wraps msg.value and approves the exact amount)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: native value wrapped for a deposit must always end the transaction attributed to a depositor; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, have the attacker run `depositNative(uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `this.balance(msg.sender)` versus `lockedAmount[msg.sender]` relation are unchanged by the attacker's transaction.
