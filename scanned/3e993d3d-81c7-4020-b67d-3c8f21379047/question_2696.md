# Q2696: AnkrBNBPoolHelper.depositLP - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/AnkrBNBPoolHelper.sol - the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Can an unprivileged attacker controlling _lpAmount, under the caller sets _minAmount to zero on the withdrawal leg, exploit this through `depositLP(uint256 _lpAmount)` to break the reconciliation between `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` and the invariant that a helper must revalidate the pool identity it acts on before moving value, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets _minAmount to zero on the withdrawal leg, call `depositLP(uint256 _lpAmount)`, and assert `_minimumLiquidity supplied by the caller` equals `the LP actually minted by the Wombat pool` and that no account can withdraw more than it put in.
