# Q3899: WombatPoolHelper.deposit - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
Note that in wombat/WombatPoolHelper.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Can an attacker holding only tokens bought on market reach it via `deposit(uint256 _amount, uint256 _minimumLiquidity)` under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes and force `pid cached at construction` apart from `pools[lpToken].pid in WombatStaking`, breaking the invariant that a helper must revalidate the pool identity it acts on before moving value for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `deposit(uint256 _amount, uint256 _minimumLiquidity)`: constrain the setup so that the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, fuzz the attacker inputs (_amount and _minimumLiquidity, forwarded verbatim into the Wombat pool), and assert after every call that a helper must revalidate the pool identity it acts on before moving value.
