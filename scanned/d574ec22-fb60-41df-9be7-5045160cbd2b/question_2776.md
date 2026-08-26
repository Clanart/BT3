# Q2776: AnkrBNBPoolHelper.depositNative - depositNative wraps msg.value and approves the exact amount

## Question
Consider wombat/AnkrBNBPoolHelper.sol, where depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Assuming the caller sets _minAmount to zero on the withdrawal leg, can an unprivileged attacker turn this into a divergence between `this.balance(msg.sender)` and `lockedAmount[msg.sender]` via `depositNative(uint256 _minimumLiquidity)`, breaking the invariant that native value wrapped for a deposit must always end the transaction attributed to a depositor and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: depositNative wraps msg.value and approves the exact amount)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: native value wrapped for a deposit must always end the transaction attributed to a depositor; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets _minAmount to zero on the withdrawal leg, call `depositNative(uint256 _minimumLiquidity)`, and assert `this.balance(msg.sender)` equals `lockedAmount[msg.sender]` and that no account can withdraw more than it put in.
