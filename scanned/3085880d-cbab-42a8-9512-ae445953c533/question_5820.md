# Q5820: MasterMagpie.emergencyWithdraw - emergencyWithdraw transfers before finishing state writes and has no nonReentrant

## Question
Consider rewards/MasterMagpie.sol, where emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Assuming the victim has a large unClaimedMgp balance that has not been settled for several epochs, can an unprivileged attacker turn this into a divergence between `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` and `block.timestamp` via `emergencyWithdraw(address _stakingToken)`, breaking the invariant that the staked-balance bookkeeping must be fully settled before any external token call in the same function and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw transfers before finishing state writes and has no nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: the staked-balance bookkeeping must be fully settled before any external token call in the same function; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unClaimedMgp balance that has not been settled for several epochs, call `emergencyWithdraw(address _stakingToken)`, and assert `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` equals `block.timestamp` and that no account can withdraw more than it put in.
