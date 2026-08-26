# Q5109: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips the base rewarder

## Question
rewards/MasterMagpie.sol: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. With _stakingToken and the exact block in which the pool is paused under attacker control and the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, can an unprivileged caller sequence `emergencyWithdraw(address _stakingToken)` so that `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)` no longer reconcile, violating the invariant that any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips the base rewarder)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() reduces user.available and user.amount and recomputes rewardDebt but never calls _harvestBaseRewarder(), so the rewarder later prices the user's accrued bonus tokens against the already-reduced stake. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: any change to UserInfo.amount must be preceded by a rewarder updateFor at the pre-change balance; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, have the attacker run `emergencyWithdraw(address _stakingToken)`, then assert the victim's claimable value and the `mgpPerSec` versus `IERC20(mgp).balanceOf(masterMagpie)` relation are unchanged by the attacker's transaction.
