# Q0354: AnkrBNBPoolHelper.depositLP - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
Note that in wombat/AnkrBNBPoolHelper.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Can an attacker holding only tokens bought on market reach it via `depositLP(uint256 _lpAmount)` under the pool's deposit token is wBNB and the caller arrived through depositNative and force `_liquidity burned via burnReceiptToken` apart from `the deposit-token balance delta paid out by WombatStaking.withdraw`, breaking the invariant that a helper must revalidate the pool identity it acts on before moving value for Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool's deposit token is wBNB and the caller arrived through depositNative, call `depositLP(uint256 _lpAmount)`, and assert `_liquidity burned via burnReceiptToken` equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and that no account can withdraw more than it put in.
