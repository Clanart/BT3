# Q2927: WombatPoolHelperV2.depositNative - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
In wombat/WombatPoolHelperV2.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Does `depositNative(uint256 _minimumLiquidity)` let an unprivileged caller exploit that under the caller sets _minAmount to zero on the withdrawal leg, so that `IERC20(stakingToken).balanceOf(address(this)) delta` diverges from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, the invariant that a helper must revalidate the pool identity it acts on before moving value is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets _minAmount to zero on the withdrawal leg, call `depositNative(uint256 _minimumLiquidity)`, and assert `IERC20(stakingToken).balanceOf(address(this)) delta` equals `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` and that no account can withdraw more than it put in.
