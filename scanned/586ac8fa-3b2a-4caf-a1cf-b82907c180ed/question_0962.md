# Q0962: MasterMagpie.emergencyWithdraw - emergencyWithdraw transfers before finishing state writes and has no nonReentrant

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Can an unprivileged attacker reach this through `emergencyWithdraw(address _stakingToken)` while the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, and drive `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` out of agreement with `block.timestamp` - breaking the invariant that the staked-balance bookkeeping must be fully settled before any external token call in the same function - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw transfers before finishing state writes and has no nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Precondition: the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it.
- Invariant to test: the staked-balance bookkeeping must be fully settled before any external token call in the same function; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, have the attacker run `emergencyWithdraw(address _stakingToken)`, then assert the victim's claimable value and the `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` versus `block.timestamp` relation are unchanged by the attacker's transaction.
