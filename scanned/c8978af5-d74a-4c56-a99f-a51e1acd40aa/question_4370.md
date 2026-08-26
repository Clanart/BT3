# Q4370: WombatPoolHelperV2.depositNative - depositNative wraps msg.value and approves the exact amount

## Question
In wombat/WombatPoolHelperV2.sol, depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Does `depositNative(uint256 _minimumLiquidity)` let an unprivileged caller exploit that under the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, so that `IERC20(stakingToken).balanceOf(address(this)) delta` diverges from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, the invariant that native value wrapped for a deposit must always end the transaction attributed to a depositor is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: depositNative wraps msg.value and approves the exact amount)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body.
- Invariant to test: native value wrapped for a deposit must always end the transaction attributed to a depositor; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `depositNative(uint256 _minimumLiquidity)`: constrain the setup so that the pool's rewarder is a V1 rewards/BaseRewardPool.sol with an empty getRewards body, fuzz the attacker inputs (msg.value and _minimumLiquidity), and assert after every call that native value wrapped for a deposit must always end the transaction attributed to a depositor.
