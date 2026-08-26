# Q0204: BribeRewardPool.withdrawFor - _getReward clears entitlement before the transfer settles

## Question
Note that in rewards/BribeRewardPool.sol, _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. Can an attacker holding only tokens bought on market reach it via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` under a large bribe for the gauge is pending and no cast has run yet and force `userRewards[_rewardToken][account]` apart from `earned(account,_rewardToken)`, breaking the invariant that an entitlement may only be cleared once the exact amount has been delivered for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: _getReward clears entitlement before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: _getReward() reads earned(), writes userRewards[token][account] = 0 and then calls safeTransfer, so an under-delivering or reverting bribe token leaves the entitlement cleared with nothing received. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`: constrain the setup so that a large bribe for the gauge is pending and no cast has run yet, fuzz the attacker inputs (the negative delta and whether the claim leg runs), and assert after every call that an entitlement may only be cleared once the exact amount has been delivered.
