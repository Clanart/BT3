# Q3084: vlMGPBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
In rewards/vlMGPBaseRewarder.sol, queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Can an unprivileged attacker reach this through `getReward(address _account, address _receiver)` while a large MGP distribution has just been queued and no account has settled yet, and drive `rewards[_rewardToken].historicalRewards` out of agreement with `IERC20(_rewardToken).balanceOf(address(this))` - breaking the invariant that a single misbehaving reward token must not block settlement of the remaining tokens - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _account, address _receiver)` sequence atomically under a large MGP distribution has just been queued and no account has settled yet, asserting at the end that `rewards[_rewardToken].historicalRewards` still equals `IERC20(_rewardToken).balanceOf(address(this))` and the PoC's balance delta is non-positive.
