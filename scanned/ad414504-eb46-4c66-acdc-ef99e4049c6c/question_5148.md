# Q5148: WombatPoolHelperV2.withdraw - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
In wombat/WombatPoolHelperV2.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Starting from a state where the attacker has moved the wom/mWom Wombat pool immediately before calling, can an unprivileged EOA use `withdraw(uint256 _liquidity, uint256 _minAmount)` to leave `IERC20(stakingToken).balanceOf(address(this)) delta` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, violating the invariant that a helper must revalidate the pool identity it acts on before moving value and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has moved the wom/mWom Wombat pool immediately before calling, call `withdraw(uint256 _liquidity, uint256 _minAmount)`, and assert `IERC20(stakingToken).balanceOf(address(this)) delta` equals `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` and that no account can withdraw more than it put in.
