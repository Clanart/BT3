# Q2274: WombatPoolHelperV2.depositNative - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
In wombat/WombatPoolHelperV2.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Can an unprivileged attacker reach this through `depositNative(uint256 _minimumLiquidity)` while the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, and drive `pid cached at construction` out of agreement with `pools[lpToken].pid in WombatStaking` - breaking the invariant that a helper must revalidate the pool identity it acts on before moving value - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, have the attacker run `depositNative(uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `pid cached at construction` versus `pools[lpToken].pid in WombatStaking` relation are unchanged by the attacker's transaction.
