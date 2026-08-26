# Q0478: AnkrBNBPoolHelper.depositNative - depositNative wraps msg.value and approves the exact amount

## Question
In wombat/AnkrBNBPoolHelper.sol, depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Can an unprivileged attacker reach this through `depositNative(uint256 _minimumLiquidity)` while the pool's deposit token is wBNB and the caller arrived through depositNative, and drive `pid cached at construction` out of agreement with `pools[lpToken].pid in WombatStaking` - breaking the invariant that native value wrapped for a deposit must always end the transaction attributed to a depositor - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: depositNative wraps msg.value and approves the exact amount)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: native value wrapped for a deposit must always end the transaction attributed to a depositor; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool's deposit token is wBNB and the caller arrived through depositNative, then assert `pid cached at construction` and `pools[lpToken].pid in WombatStaking` end identical in both runs.
