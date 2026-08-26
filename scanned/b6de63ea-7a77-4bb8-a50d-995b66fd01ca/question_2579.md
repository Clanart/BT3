# Q2579: ReferralStorage.claimReward - claimReward transfers before zeroing the balance

## Question
In rewards/ReferralStorage.sol, claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Does `claimReward()` let an unprivileged caller exploit that under the attacker cancels a cooldown so their real lock rises with no factor refresh, so that `userInfos[account].factor` diverges from `totalBoostFactor`, the invariant that an accrued balance must be zeroed before the transfer that pays it is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: claimReward transfers before zeroing the balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: an accrued balance must be zeroed before the transfer that pays it; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `claimReward()`: constrain the setup so that the attacker cancels a cooldown so their real lock rises with no factor refresh, fuzz the attacker inputs (the moment the accrued MGP is drawn from the shared contract balance), and assert after every call that an accrued balance must be zeroed before the transfer that pays it.
