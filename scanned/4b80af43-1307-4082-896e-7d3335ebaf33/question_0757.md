# Q0757: AnkrBNBPoolHelper.withdraw - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
In wombat/AnkrBNBPoolHelper.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Starting from a state where the pool's deposit token is wBNB and the caller arrived through depositNative, can an unprivileged EOA use `withdraw(uint256 _liquidity, uint256 _minAmount)` to leave `IERC20(stakingToken).totalSupply()` inconsistent with `the MasterWombat staked balance for pid`, violating the invariant that a helper must revalidate the pool identity it acts on before moving value and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the pool's deposit token is wBNB and the caller arrived through depositNative, have the attacker run `withdraw(uint256 _liquidity, uint256 _minAmount)`, then assert the victim's claimable value and the `IERC20(stakingToken).totalSupply()` versus `the MasterWombat staked balance for pid` relation are unchanged by the attacker's transaction.
