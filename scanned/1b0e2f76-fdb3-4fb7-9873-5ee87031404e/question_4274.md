# Q4274: mWOMSVBaseRewarder.getReward - unbounded rewardTokens array blocks the claim-all path

## Question
Note that in rewards/mWOMSVBaseRewarder.sol, queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Can an attacker holding only tokens bought on market reach it via `getReward(address _account, address _receiver)` under the attacker locks one block before a known large settlement and unlocks one block after and force `totalStaked()` apart from `IERC20(mWOMSV).totalSupply()`, breaking the invariant that a single misbehaving reward token must not block settlement of the remaining tokens for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: unbounded rewardTokens array blocks the claim-all path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: queueNewRewards pushes new reward tokens with no removal path and getReward() iterates the whole array, so one reward token that begins reverting on transfer disables settlement of all the others for every user. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: a single misbehaving reward token must not block settlement of the remaining tokens; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker locks one block before a known large settlement and unlocks one block after, snapshot `totalStaked()` and `IERC20(mWOMSV).totalSupply()`, run the attacker's `getReward(address _account, address _receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
