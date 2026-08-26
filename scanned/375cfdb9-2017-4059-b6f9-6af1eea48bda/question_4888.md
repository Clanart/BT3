# Q4888: vlMGPBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
rewards/vlMGPBaseRewarder.sol - queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Can an unprivileged attacker controlling the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path, under a registered reward token has begun reverting on transfer, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `forfeitAmount` and `rewardInfo.rewardPerTokenStored` and the invariant that a single misbehaving reward token must not block settlement of the remaining tokens, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up a registered reward token has begun reverting on transfer, snapshot `forfeitAmount` and `rewardInfo.rewardPerTokenStored`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
