# Q2010: MasterMagpie.emergencyWithdraw - emergencyWithdraw transfers before finishing state writes and has no nonReentrant

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Can an unprivileged attacker reach this through `emergencyWithdraw(address _stakingToken)` while the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, and drive `IBaseRewardPool(rewarder).balanceOf(user)` out of agreement with `IBaseRewardPool(rewarder).totalStaked()` - breaking the invariant that the staked-balance bookkeeping must be fully settled before any external token call in the same function - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw transfers before finishing state writes and has no nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: the staked-balance bookkeeping must be fully settled before any external token call in the same function; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `emergencyWithdraw(address _stakingToken)` sequence atomically under the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, asserting at the end that `IBaseRewardPool(rewarder).balanceOf(user)` still equals `IBaseRewardPool(rewarder).totalStaked()` and the PoC's balance delta is non-positive.
