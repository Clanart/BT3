# Q2715: WombatPoolHelperV2.depositFor - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/WombatPoolHelperV2.sol: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Under the caller sets _minAmount to zero on the withdrawal leg, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for)` that leaves `_minimumLiquidity supplied by the caller` unreconciled with `the LP actually minted by the Wombat pool`, violates the invariant that a helper must revalidate the pool identity it acts on before moving value, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any address) and _amount, with _minimumLiquidity hardcoded to zero) under the caller sets _minAmount to zero on the withdrawal leg, asserting on every row that a helper must revalidate the pool identity it acts on before moving value.
