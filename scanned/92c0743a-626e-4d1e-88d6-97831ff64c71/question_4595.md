# Q4595: mWOMSVBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
rewards/mWOMSVBaseRewarder.sol: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Under the victim has not settled for several epochs and holds a large userRewards balance, is there an unprivileged sequence of `getReward(address _account, address _receiver)` that leaves `balanceOf(account)` unreconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, violates the invariant that a single misbehaving reward token must not block settlement of the remaining tokens, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the victim has not settled for several epochs and holds a large userRewards balance, snapshot `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
