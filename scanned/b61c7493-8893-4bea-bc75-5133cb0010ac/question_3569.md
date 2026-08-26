# Q3569: MasterMagpie.emergencyWithdraw - emergencyWithdraw transfers before finishing state writes and has no nonReentrant

## Question
rewards/MasterMagpie.sol: emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Under a large honest deposit is sitting in the mempool and the attacker sandwiches it, is there an unprivileged sequence of `emergencyWithdraw(address _stakingToken)` that leaves `vlmgp.totalSupply()` unreconciled with `sum of userInfo[vlmgp][*].amount`, violates the invariant that the staked-balance bookkeeping must be fully settled before any external token call in the same function, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw transfers before finishing state writes and has no nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() performs safeTransfer of the staking token before it updates user.amount and rewardDebt, and unlike deposit/withdraw it carries no nonReentrant modifier, so a staking token with a transfer hook re-enters with user.amount still holding the pre-withdrawal value. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: the staked-balance bookkeeping must be fully settled before any external token call in the same function; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `emergencyWithdraw(address _stakingToken)` sequence atomically under a large honest deposit is sitting in the mempool and the attacker sandwiches it, asserting at the end that `vlmgp.totalSupply()` still equals `sum of userInfo[vlmgp][*].amount` and the PoC's balance delta is non-positive.
