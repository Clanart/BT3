# Q4769: AnkrBNBPoolHelper.harvest - harvest is permissionless and drives the fee and conversion legs

## Question
In wombat/AnkrBNBPoolHelper.sol, harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Starting from a state where an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, can an unprivileged EOA use `harvest()` to leave `IERC20(stakingToken).balanceOf(address(this)) delta` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, violating the invariant that the timing of fee conversion for a pool must not be selectable by an unrelated party and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `harvest()` (mechanism: harvest is permissionless and drives the fee and conversion legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: the timing of fee conversion for a pool must not be selectable by an unrelated party; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, call `harvest()`, and assert `IERC20(stakingToken).balanceOf(address(this)) delta` equals `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` and that no account can withdraw more than it put in.
