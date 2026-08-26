# Q5163: AnkrBNBPoolHelper.deposit - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
Note that in wombat/AnkrBNBPoolHelper.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Can an attacker holding only tokens bought on market reach it via `deposit(uint256 _amount, uint256 _minimumLiquidity)` under the attacker deposits and withdraws through the helper inside one transaction and force `IERC20(stakingToken).totalSupply()` apart from `the MasterWombat staked balance for pid`, breaking the invariant that a helper must revalidate the pool identity it acts on before moving value for Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker deposits and withdraws through the helper inside one transaction, then assert `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` end identical in both runs.
