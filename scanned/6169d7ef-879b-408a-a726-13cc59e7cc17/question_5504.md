# Q5504: MasterMagpie.multiclaim - rewardDebt reset without reward payout in _multiClaim

## Question
In rewards/MasterMagpie.sol, _multiClaim() sets user.rewardDebt = user.amount * accMGPPerShare / 1e12 and zeroes unClaimedMgp for every entry in the caller-supplied _stakingTokens array before the send branch decides where the MGP goes, so a claim path that silently pays nothing still burns the accrual. Starting from a state where the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, can an unprivileged EOA use `multiclaim(address[] _stakingTokens)` to leave `userInfo[_stakingToken][user].rewardDebt` inconsistent with `tokenToPoolInfo[_stakingToken].accMGPPerShare`, violating the invariant that no code path may advance rewardDebt or clear unClaimedMgp unless the corresponding MGP actually leaves the contract to the user or is locked for them and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: rewardDebt reset without reward payout in _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _multiClaim() sets user.rewardDebt = user.amount * accMGPPerShare / 1e12 and zeroes unClaimedMgp for every entry in the caller-supplied _stakingTokens array before the send branch decides where the MGP goes, so a claim path that silently pays nothing still burns the accrual. Precondition: the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp.
- Invariant to test: no code path may advance rewardDebt or clear unClaimedMgp unless the corresponding MGP actually leaves the contract to the user or is locked for them; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `multiclaim(address[] _stakingTokens)` sequence atomically under the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, asserting at the end that `userInfo[_stakingToken][user].rewardDebt` still equals `tokenToPoolInfo[_stakingToken].accMGPPerShare` and the PoC's balance delta is non-positive.
