# Q0291: WombatPoolHelperV2.depositFor - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/WombatPoolHelperV2.sol: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Under the pool's deposit token is wBNB and the caller arrived through depositNative, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for)` that leaves `_liquidity burned via burnReceiptToken` unreconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`, violates the invariant that a helper must revalidate the pool identity it acts on before moving value, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool's deposit token is wBNB and the caller arrived through depositNative, call `depositFor(uint256 _amount, address _for)`, and assert `_liquidity burned via burnReceiptToken` equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and that no account can withdraw more than it put in.
