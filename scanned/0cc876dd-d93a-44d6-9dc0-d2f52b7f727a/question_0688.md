# Q0688: vlMGPBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
rewards/vlMGPBaseRewarder.sol - queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Can an unprivileged attacker controlling the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path, under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` and the invariant that a single misbehaving reward token must not block settlement of the remaining tokens, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `userRewards[_rewardToken][account]` versus `rewards[_rewardToken].rewardPerTokenStored` relation are unchanged by the attacker's transaction.
