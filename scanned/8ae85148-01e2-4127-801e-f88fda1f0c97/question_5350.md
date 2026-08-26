# Q5350: WombatPoolHelper.deposit - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/WombatPoolHelper.sol: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. With _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool under attacker control and the attacker deposits and withdraws through the helper inside one transaction, can an unprivileged caller sequence `deposit(uint256 _amount, uint256 _minimumLiquidity)` so that `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` no longer reconcile, violating the invariant that a helper must revalidate the pool identity it acts on before moving value and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker deposits and withdraws through the helper inside one transaction, call `deposit(uint256 _amount, uint256 _minimumLiquidity)`, and assert `IERC20(stakingToken).totalSupply()` equals `the MasterWombat staked balance for pid` and that no account can withdraw more than it put in.
