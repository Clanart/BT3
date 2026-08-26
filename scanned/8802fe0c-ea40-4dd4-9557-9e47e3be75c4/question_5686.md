# Q5686: WombatPoolHelper.deposit - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/WombatPoolHelper.sol: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. With _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool under attacker control and MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed, can an unprivileged caller sequence `deposit(uint256 _amount, uint256 _minimumLiquidity)` so that `pid cached at construction` and `pools[lpToken].pid in WombatStaking` no longer reconcile, violating the invariant that a helper must revalidate the pool identity it acts on before moving value and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed, then assert `pid cached at construction` and `pools[lpToken].pid in WombatStaking` end identical in both runs.
