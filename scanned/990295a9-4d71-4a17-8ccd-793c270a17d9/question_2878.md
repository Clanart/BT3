# Q2878: MasterMagpie.emergencyWithdraw - emergencyWithdraw transfers before finishing state writes and has no nonReentrant

## Question
rewards/MasterMagpie.sol - emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Can an unprivileged attacker controlling _stakingToken and the exact block in which the pool is paused, under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, exploit this through `emergencyWithdraw(address _stakingToken)` to break the reconciliation between `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint` and the invariant that the staked-balance bookkeeping must be fully settled before any external token call in the same function, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw transfers before finishing state writes and has no nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: the staked-balance bookkeeping must be fully settled before any external token call in the same function; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, have the attacker run `emergencyWithdraw(address _stakingToken)`, then assert the victim's claimable value and the `totalAllocPoint` versus `tokenToPoolInfo[_stakingToken].allocPoint` relation are unchanged by the attacker's transaction.
