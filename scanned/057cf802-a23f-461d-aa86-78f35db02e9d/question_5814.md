# Q5814: MasterMagpie.multiclaimFor - referral trigger fired on attacker-chosen victims

## Question
In rewards/MasterMagpie.sol, _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Can an unprivileged attacker reach this through `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` while the victim has a large unClaimedMgp balance that has not been settled for several epochs, and drive `mgpPerSec` out of agreement with `IERC20(mgp).balanceOf(masterMagpie)` - breaking the invariant that referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: referral trigger fired on attacker-chosen victims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the victim has a large unClaimedMgp balance that has not been settled for several epochs, snapshot `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)`, run the attacker's `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
