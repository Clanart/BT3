# Q1587: vlMGPBaseRewarder.updateFor - early-continue skips a real balance change

## Question
rewards/vlMGPBaseRewarder.sol - _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Can an unprivileged attacker controlling the victim address and the block at which their index is pinned, under the computed forfeit lands just below the _amount / 1000 dust threshold, exploit this through `updateFor(address _account)` to break the reconciliation between `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` and the invariant that userRewards must capture every balance-weighted segment even when the global index did not move, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the computed forfeit lands just below the _amount / 1000 dust threshold, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that userRewards must capture every balance-weighted segment even when the global index did not move.
