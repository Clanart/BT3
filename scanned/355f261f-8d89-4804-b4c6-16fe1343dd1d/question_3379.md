# Q3379: WombatPoolHelperV2.depositLP - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
In wombat/WombatPoolHelperV2.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Can an unprivileged attacker reach this through `depositLP(uint256 _lpAmount)` while a residual stakingToken balance from an earlier rounding sits on the helper, and drive `IERC20(stakingToken).balanceOf(address(this)) delta` out of agreement with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` - breaking the invariant that a helper must revalidate the pool identity it acts on before moving value - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount) under a residual stakingToken balance from an earlier rounding sits on the helper, asserting on every row that a helper must revalidate the pool identity it acts on before moving value.
