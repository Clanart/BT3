# Q3847: mWOMSVBaseRewarder.getReward - InvalidRewardableAmount revert bricks a user's claims

## Question
In rewards/mWOMSVBaseRewarder.sol, _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside mWOMSV from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under totalStaked is zero and queuedRewards holds a backlog, so that `userRewards[_rewardToken][account]` diverges from `rewards[_rewardToken].rewardPerTokenStored`, the invariant that a pricing helper on the claim path must never be able to permanently block settlement is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: InvalidRewardableAmount revert bricks a user's claims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() reverts with InvalidRewardableAmount whenever rewardableAmount exceeds _amount, and rewardablePercentWAD is computed inside mWOMSV from getUserTotalLocked, which itself can underflow, so a single inconsistent lock state makes every claim path for that user revert forever. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: a pricing helper on the claim path must never be able to permanently block settlement; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up totalStaked is zero and queuedRewards holds a backlog, snapshot `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
