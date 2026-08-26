# Q4081: WombatPoolHelperV2.harvest - deposit and withdraw both run the full harvest and fee path

## Question
In wombat/WombatPoolHelperV2.sol, WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Does `harvest()` let an unprivileged caller exploit that under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, so that `IERC20(stakingToken).balanceOf(address(this)) delta` diverges from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, the invariant that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `harvest()` (mechanism: deposit and withdraw both run the full harvest and fee path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, have the attacker run `harvest()`, then assert the victim's claimable value and the `IERC20(stakingToken).balanceOf(address(this)) delta` versus `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` relation are unchanged by the attacker's transaction.
