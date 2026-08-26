# Q5980: MasterMagpie.emergencyWithdraw - emergencyWithdraw transfers before finishing state writes and has no nonReentrant

## Question
Consider rewards/MasterMagpie.sol, where emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Assuming the attacker repeats the call in the same block to observe the second, no-op iteration, can an unprivileged attacker turn this into a divergence between `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint` via `emergencyWithdraw(address _stakingToken)`, breaking the invariant that the staked-balance bookkeeping must be fully settled before any external token call in the same function and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw transfers before finishing state writes and has no nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: the staked-balance bookkeeping must be fully settled before any external token call in the same function; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `emergencyWithdraw(address _stakingToken)`: constrain the setup so that the attacker repeats the call in the same block to observe the second, no-op iteration, fuzz the attacker inputs (_stakingToken and the exact block in which the pool is paused), and assert after every call that the staked-balance bookkeeping must be fully settled before any external token call in the same function.
