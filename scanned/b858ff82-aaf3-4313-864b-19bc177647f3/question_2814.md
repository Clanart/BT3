# Q2814: AnkrBNBPoolHelper.depositNative - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
In wombat/AnkrBNBPoolHelper.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Starting from a state where the caller sets _minAmount to zero on the withdrawal leg, can an unprivileged EOA use `depositNative(uint256 _minimumLiquidity)` to leave `pid cached at construction` inconsistent with `pools[lpToken].pid in WombatStaking`, violating the invariant that a helper must revalidate the pool identity it acts on before moving value and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the caller sets _minAmount to zero on the withdrawal leg, snapshot `pid cached at construction` and `pools[lpToken].pid in WombatStaking`, run the attacker's `depositNative(uint256 _minimumLiquidity)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
