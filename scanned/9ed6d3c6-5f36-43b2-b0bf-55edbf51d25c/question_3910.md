# Q3910: vlMGPBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
Consider rewards/vlMGPBaseRewarder.sol, where queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Assuming totalStaked is zero and queuedRewards holds a backlog, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` via `getReward(address _account, address _receiver)`, breaking the invariant that a single misbehaving reward token must not block settlement of the remaining tokens and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under totalStaked is zero and queuedRewards holds a backlog, asserting on every row that a single misbehaving reward token must not block settlement of the remaining tokens.
