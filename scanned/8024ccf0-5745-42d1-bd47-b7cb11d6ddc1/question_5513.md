# Q5513: WombatPoolHelperV2.depositNative - depositNative wraps msg.value and approves the exact amount

## Question
wombat/WombatPoolHelperV2.sol - depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Can an unprivileged attacker controlling msg.value and _minimumLiquidity, under the receipt token is minted to the helper while the credit is directed at a different address, exploit this through `depositNative(uint256 _minimumLiquidity)` to break the reconciliation between `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` and the invariant that native value wrapped for a deposit must always end the transaction attributed to a depositor, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: depositNative wraps msg.value and approves the exact amount)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: native value wrapped for a deposit must always end the transaction attributed to a depositor; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the receipt token is minted to the helper while the credit is directed at a different address, call `depositNative(uint256 _minimumLiquidity)`, and assert `_minimumLiquidity supplied by the caller` equals `the LP actually minted by the Wombat pool` and that no account can withdraw more than it put in.
