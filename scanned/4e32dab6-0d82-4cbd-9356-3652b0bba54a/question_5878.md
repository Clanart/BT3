# Q5878: MasterMagpie.multiclaimFor - rewardDebt reset without reward payout in _multiClaim

## Question
In rewards/MasterMagpie.sol, _multiClaim() sets user.rewardDebt = user.amount * accMGPPerShare / 1e12 and zeroes unClaimedMgp for every entry in the caller-supplied _stakingTokens array before the send branch decides where the MGP goes, so a claim path that silently pays nothing still burns the accrual. Starting from a state where the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, can an unprivileged EOA use `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` to leave `totalAllocPoint` inconsistent with `tokenToPoolInfo[_stakingToken].allocPoint`, violating the invariant that no code path may advance rewardDebt or clear unClaimedMgp unless the corresponding MGP actually leaves the contract to the user or is locked for them and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: rewardDebt reset without reward payout in _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() sets user.rewardDebt = user.amount * accMGPPerShare / 1e12 and zeroes unClaimedMgp for every entry in the caller-supplied _stakingTokens array before the send branch decides where the MGP goes, so a claim path that silently pays nothing still burns the accrual. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: no code path may advance rewardDebt or clear unClaimedMgp unless the corresponding MGP actually leaves the contract to the user or is locked for them; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, then assert `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint` end identical in both runs.
