# Q4594: vlMGPBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
Note that in rewards/vlMGPBaseRewarder.sol, queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under the victim has not settled for several epochs and holds a large userRewards balance and force `balanceOf(account)` apart from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, breaking the invariant that a single misbehaving reward token must not block settlement of the remaining tokens for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under the victim has not settled for several epochs and holds a large userRewards balance, asserting on every row that a single misbehaving reward token must not block settlement of the remaining tokens.
