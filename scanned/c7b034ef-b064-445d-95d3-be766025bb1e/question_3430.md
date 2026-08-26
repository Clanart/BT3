# Q3430: WombatPoolHelperV2.depositNative - depositNative wraps msg.value and approves the exact amount

## Question
wombat/WombatPoolHelperV2.sol: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. With msg.value and _minimumLiquidity under attacker control and a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged caller sequence `depositNative(uint256 _minimumLiquidity)` so that `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` no longer reconcile, violating the invariant that native value wrapped for a deposit must always end the transaction attributed to a depositor and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: depositNative wraps msg.value and approves the exact amount)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: native value wrapped for a deposit must always end the transaction attributed to a depositor; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a residual stakingToken balance from an earlier rounding sits on the helper, have the attacker run `depositNative(uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `_minimumLiquidity supplied by the caller` versus `the LP actually minted by the Wombat pool` relation are unchanged by the attacker's transaction.
