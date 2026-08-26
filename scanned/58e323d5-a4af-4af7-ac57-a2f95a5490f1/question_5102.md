# Q5102: MasterMagpie.multiclaimFor - referral trigger fired on attacker-chosen victims

## Question
In rewards/MasterMagpie.sol, _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Can an unprivileged attacker reach this through `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` while the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, and drive `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` out of agreement with `block.timestamp` - breaking the invariant that referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: referral trigger fired on attacker-chosen victims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, have the attacker run `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, then assert the victim's claimable value and the `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` versus `block.timestamp` relation are unchanged by the attacker's transaction.
