# Q3521: MasterMagpie.multiclaimFor - referral trigger fired on attacker-chosen victims

## Question
In rewards/MasterMagpie.sol, _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Starting from a state where a large honest deposit is sitting in the mempool and the attacker sandwiches it, can an unprivileged EOA use `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` to leave `userInfo[_stakingToken][user].rewardDebt` inconsistent with `tokenToPoolInfo[_stakingToken].accMGPPerShare`, violating the invariant that referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: referral trigger fired on attacker-chosen victims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large honest deposit is sitting in the mempool and the attacker sandwiches it, then assert `userInfo[_stakingToken][user].rewardDebt` and `tokenToPoolInfo[_stakingToken].accMGPPerShare` end identical in both runs.
