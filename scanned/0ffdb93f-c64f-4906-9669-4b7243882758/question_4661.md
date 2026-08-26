# Q4661: AnkrBNBPoolHelper.depositNative - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
Consider wombat/AnkrBNBPoolHelper.sol, where the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Assuming an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, can an unprivileged attacker turn this into a divergence between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` via `depositNative(uint256 _minimumLiquidity)`, breaking the invariant that a helper must revalidate the pool identity it acts on before moving value and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, then assert `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` end identical in both runs.
