# Q4273: vlMGPBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
In rewards/vlMGPBaseRewarder.sol, queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Does `getReward(address _account, address _receiver)` let an unprivileged caller exploit that under the attacker locks one block before a known large settlement and unlocks one block after, so that `totalStaked()` diverges from `IERC20(vlMGP).totalSupply()`, the invariant that a single misbehaving reward token must not block settlement of the remaining tokens is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path) under the attacker locks one block before a known large settlement and unlocks one block after, asserting on every row that a single misbehaving reward token must not block settlement of the remaining tokens.
