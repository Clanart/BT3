# Q5512: WombatPoolHelper.harvest - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/WombatPoolHelper.sol: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Under the attacker deposits and withdraws through the helper inside one transaction, is there an unprivileged sequence of `harvest()` that leaves `_liquidity burned via burnReceiptToken` unreconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`, violates the invariant that a helper must revalidate the pool identity it acts on before moving value, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `harvest()` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the pool's rewards are harvested and fee-split
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `harvest()`: constrain the setup so that the attacker deposits and withdraws through the helper inside one transaction, fuzz the attacker inputs (the exact block at which the pool's rewards are harvested and fee-split), and assert after every call that a helper must revalidate the pool identity it acts on before moving value.
