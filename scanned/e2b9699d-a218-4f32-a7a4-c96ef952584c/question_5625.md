# Q5625: MasterMagpie.emergencyWithdraw - emergencyWithdraw transfers before finishing state writes and has no nonReentrant

## Question
rewards/MasterMagpie.sol: emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Under the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, is there an unprivileged sequence of `emergencyWithdraw(address _stakingToken)` that leaves `unClaimedMgp[_stakingToken][user]` unreconciled with `userInfo[_stakingToken][user].rewardDebt`, violates the invariant that the staked-balance bookkeeping must be fully settled before any external token call in the same function, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw transfers before finishing state writes and has no nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Precondition: the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp.
- Invariant to test: the staked-balance bookkeeping must be fully settled before any external token call in the same function; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, snapshot `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt`, run the attacker's `emergencyWithdraw(address _stakingToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
