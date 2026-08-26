# Q5176: WombatPoolHelperV2.harvest - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
In wombat/WombatPoolHelperV2.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Can an unprivileged attacker reach this through `harvest()` while the attacker has moved the wom/mWom Wombat pool immediately before calling, and drive `_liquidity burned via burnReceiptToken` out of agreement with `the deposit-token balance delta paid out by WombatStaking.withdraw` - breaking the invariant that a helper must revalidate the pool identity it acts on before moving value - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `harvest()` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has moved the wom/mWom Wombat pool immediately before calling, call `harvest()`, and assert `_liquidity burned via burnReceiptToken` equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and that no account can withdraw more than it put in.
