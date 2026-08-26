# Q2378: MasterMagpie.multiclaim - rewardDebt reset without reward payout in _multiClaim

## Question
In rewards/MasterMagpie.sol, _multiClaim() sets user.rewardDebt = user.amount * accMGPPerShare / 1e12 and zeroes unClaimedMgp for every entry in the caller-supplied _stakingTokens array before the send branch decides where the MGP goes, so a claim path that silently pays nothing still burns the accrual. Does `multiclaim(address[] _stakingTokens)` let an unprivileged caller exploit that under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, so that `IBaseRewardPool(rewarder).balanceOf(user)` diverges from `IBaseRewardPool(rewarder).totalStaked()`, the invariant that no code path may advance rewardDebt or clear unClaimedMgp unless the corresponding MGP actually leaves the contract to the user or is locked for them is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: rewardDebt reset without reward payout in _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _multiClaim() sets user.rewardDebt = user.amount * accMGPPerShare / 1e12 and zeroes unClaimedMgp for every entry in the caller-supplied _stakingTokens array before the send branch decides where the MGP goes, so a claim path that silently pays nothing still burns the accrual. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: no code path may advance rewardDebt or clear unClaimedMgp unless the corresponding MGP actually leaves the contract to the user or is locked for them; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, call `multiclaim(address[] _stakingTokens)`, and assert `IBaseRewardPool(rewarder).balanceOf(user)` equals `IBaseRewardPool(rewarder).totalStaked()` and that no account can withdraw more than it put in.
