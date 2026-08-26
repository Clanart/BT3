# Q5866: MasterMagpie.multiclaimSpec - BaseRewardPool.getRewards is an empty function body

## Question
rewards/MasterMagpie.sol: rewards/BaseRewardPool.sol implements getRewards(address,address,address[]) as an empty stub, so any multiclaimSpec/multiclaimFor call that supplies a non-empty _rewardTokens[i] for a pool wired to a V1 BaseRewardPool routes into the stub and pays nothing while _multiClaim still advances the MGP accrual. Under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, is there an unprivileged sequence of `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` that leaves `totalAllocPoint` unreconciled with `tokenToPoolInfo[_stakingToken].allocPoint`, violates the invariant that specifying reward tokens must never be weaker than the claim-all path; a claim that returns success must move the tokens it accounted for, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: BaseRewardPool.getRewards is an empty function body)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: rewards/BaseRewardPool.sol implements getRewards(address,address,address[]) as an empty stub, so any multiclaimSpec/multiclaimFor call that supplies a non-empty _rewardTokens[i] for a pool wired to a V1 BaseRewardPool routes into the stub and pays nothing while _multiClaim still advances the MGP accrual. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: specifying reward tokens must never be weaker than the claim-all path; a claim that returns success must move the tokens it accounted for; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (both outer and inner arrays, so every reward-token address and its order) under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, asserting on every row that specifying reward tokens must never be weaker than the claim-all path; a claim that returns success must move the tokens it accounted for.
