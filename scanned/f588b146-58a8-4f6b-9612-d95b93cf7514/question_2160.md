# Q2160: AnkrBNBPoolHelper.depositNative - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
In wombat/AnkrBNBPoolHelper.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Does `depositNative(uint256 _minimumLiquidity)` let an unprivileged caller exploit that under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, so that `_minimumLiquidity supplied by the caller` diverges from `the LP actually minted by the Wombat pool`, the invariant that a helper must revalidate the pool identity it acts on before moving value is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, then assert `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` end identical in both runs.
