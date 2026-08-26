# Q3162: BribeRewardPool.withdrawFor - _getReward clears entitlement before the transfer settles

## Question
rewards/BribeRewardPool.sol - _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. Can an unprivileged attacker controlling the negative delta and whether the claim leg runs, under the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, exploit this through `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` to break the reconciliation between `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` and the invariant that an entitlement may only be cleared once the exact amount has been delivered, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward clears entitlement before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. Precondition: the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, have the attacker run `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, then assert the victim's claimable value and the `rewards[_rewardToken].queuedRewards` versus `totalSupply at the moment of the flush` relation are unchanged by the attacker's transaction.
