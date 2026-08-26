# Q5613: MasterMagpie.multiclaimFor - referral trigger fired on attacker-chosen victims

## Question
rewards/MasterMagpie.sol: _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Under the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, is there an unprivileged sequence of `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` that leaves `totalAllocPoint` unreconciled with `tokenToPoolInfo[_stakingToken].allocPoint`, violates the invariant that referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: referral trigger fired on attacker-chosen victims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Precondition: the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp.
- Invariant to test: referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, snapshot `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint`, run the attacker's `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
