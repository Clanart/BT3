# Q0570: WombatPoolHelperV2.depositNative - depositNative wraps msg.value and approves the exact amount

## Question
In wombat/WombatPoolHelperV2.sol, depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Does `depositNative(uint256 _minimumLiquidity)` let an unprivileged caller exploit that under the pool's deposit token is wBNB and the caller arrived through depositNative, so that `IERC20(stakingToken).balanceOf(address(this)) delta` diverges from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, the invariant that native value wrapped for a deposit must always end the transaction attributed to a depositor is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: depositNative wraps msg.value and approves the exact amount)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: depositNative() wraps msg.value, approves wombatStaking for msg.value and then approves zero afterwards, so a Wombat deposit that consumes less than msg.value leaves wrapped native tokens on the helper with no owner. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: native value wrapped for a deposit must always end the transaction attributed to a depositor; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `depositNative(uint256 _minimumLiquidity)` sequence atomically under the pool's deposit token is wBNB and the caller arrived through depositNative, asserting at the end that `IERC20(stakingToken).balanceOf(address(this)) delta` still equals `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` and the PoC's balance delta is non-positive.
