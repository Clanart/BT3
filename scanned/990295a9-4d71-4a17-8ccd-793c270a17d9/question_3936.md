# Q3936: MasterMagpie.multiclaimSpec - BaseRewardPool.getRewards is an empty function body

## Question
In rewards/MasterMagpie.sol, rewards/BaseRewardPool.sol implements getRewards(address,address,address[]) as an empty stub, so any multiclaimSpec/multiclaimFor call that supplies a non-empty _rewardTokens[i] for a pool wired to a V1 BaseRewardPool routes into the stub and pays nothing while _multiClaim still advances the MGP accrual. Does `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` let an unprivileged caller exploit that under the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, so that `userInfo[_stakingToken][user].amount` diverges from `_calLpSupply(_stakingToken)`, the invariant that specifying reward tokens must never be weaker than the claim-all path; a claim that returns success must move the tokens it accounted for is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: BaseRewardPool.getRewards is an empty function body)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: rewards/BaseRewardPool.sol implements getRewards(address,address,address[]) as an empty stub, so any multiclaimSpec/multiclaimFor call that supplies a non-empty _rewardTokens[i] for a pool wired to a V1 BaseRewardPool routes into the stub and pays nothing while _multiClaim still advances the MGP accrual. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty.
- Invariant to test: specifying reward tokens must never be weaker than the claim-all path; a claim that returns success must move the tokens it accounted for; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, call `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`, and assert `userInfo[_stakingToken][user].amount` equals `_calLpSupply(_stakingToken)` and that no account can withdraw more than it put in.
