# Q2888: WombatPoolHelper.depositLP - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/WombatPoolHelper.sol - the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Can an unprivileged attacker controlling _lpAmount and the LP tokens pulled from the caller, under the caller sets _minAmount to zero on the withdrawal leg, exploit this through `depositLP(uint256 _lpAmount)` to break the reconciliation between `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` and the invariant that a helper must revalidate the pool identity it acts on before moving value, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `depositLP(uint256 _lpAmount)` sequence atomically under the caller sets _minAmount to zero on the withdrawal leg, asserting at the end that `_minimumLiquidity supplied by the caller` still equals `the LP actually minted by the Wombat pool` and the PoC's balance delta is non-positive.
