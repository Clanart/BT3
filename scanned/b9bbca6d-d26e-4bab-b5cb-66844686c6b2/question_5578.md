# Q5578: WombatPoolHelperV2.harvest - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
Consider wombat/WombatPoolHelperV2.sol, where the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Assuming the receipt token is minted to the helper while the credit is directed at a different address, can an unprivileged attacker turn this into a divergence between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` via `harvest()`, breaking the invariant that a helper must revalidate the pool identity it acts on before moving value and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `harvest()` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the harvest timing for the whole pool) under the receipt token is minted to the helper while the credit is directed at a different address, asserting on every row that a helper must revalidate the pool identity it acts on before moving value.
