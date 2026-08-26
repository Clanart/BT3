# Q0383: WombatPoolHelper.depositLP - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
In wombat/WombatPoolHelper.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Starting from a state where the pool's deposit token is wBNB and the caller arrived through depositNative, can an unprivileged EOA use `depositLP(uint256 _lpAmount)` to leave `_liquidity burned via burnReceiptToken` inconsistent with `the deposit-token balance delta paid out by WombatStaking.withdraw`, violating the invariant that a helper must revalidate the pool identity it acts on before moving value and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `depositLP(uint256 _lpAmount)`: constrain the setup so that the pool's deposit token is wBNB and the caller arrived through depositNative, fuzz the attacker inputs (_lpAmount and the LP tokens pulled from the caller), and assert after every call that a helper must revalidate the pool identity it acts on before moving value.
