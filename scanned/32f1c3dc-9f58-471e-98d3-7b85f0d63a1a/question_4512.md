# Q4512: WombatPoolHelper.depositNative - depositNative wraps msg.value and approves the exact amount

## Question
Consider wombat/WombatPoolHelper.sol, where depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Assuming the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, can an unprivileged attacker turn this into a divergence between `pid cached at construction` and `pools[lpToken].pid in WombatStaking` via `depositNative(uint256 _minimumLiquidity)`, breaking the invariant that native value wrapped for a deposit must always end the transaction attributed to a depositor and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: depositNative wraps msg.value and approves the exact amount)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: native value wrapped for a deposit must always end the transaction attributed to a depositor; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `depositNative(uint256 _minimumLiquidity)` sequence atomically under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, asserting at the end that `pid cached at construction` still equals `pools[lpToken].pid in WombatStaking` and the PoC's balance delta is non-positive.
