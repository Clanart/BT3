# Q4253: WombatPoolHelperV2.depositFor - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/WombatPoolHelperV2.sol: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. With _for (any address) and _amount, with _minimumLiquidity hardcoded to zero under attacker control and the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, can an unprivileged caller sequence `depositFor(uint256 _amount, address _for)` so that `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` no longer reconcile, violating the invariant that a helper must revalidate the pool identity it acts on before moving value and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, snapshot `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw`, run the attacker's `depositFor(uint256 _amount, address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
