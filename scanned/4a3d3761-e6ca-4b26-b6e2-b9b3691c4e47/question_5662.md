# Q5662: WombatPoolHelper.harvest - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
In wombat/WombatPoolHelper.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Does `harvest()` let an unprivileged caller exploit that under the receipt token is minted to the helper while the credit is directed at a different address, so that `this.balance(msg.sender)` diverges from `lockedAmount[msg.sender]`, the invariant that a helper must revalidate the pool identity it acts on before moving value is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `harvest()` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the pool's rewards are harvested and fee-split
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the pool's rewards are harvested and fee-split) under the receipt token is minted to the helper while the credit is directed at a different address, asserting on every row that a helper must revalidate the pool identity it acts on before moving value.
