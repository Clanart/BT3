# Q4115: ReferralStorage.claimReward - claimReward transfers before zeroing the balance

## Question
In rewards/ReferralStorage.sol, claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Does `claimReward()` let an unprivileged caller exploit that under sharePercent is set so most of the split goes to the referrer, so that `codeOwners[_code]` diverges from `userInfos[account].myCode`, the invariant that an accrued balance must be zeroed before the transfer that pays it is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: claimReward transfers before zeroing the balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Precondition: sharePercent is set so most of the split goes to the referrer.
- Invariant to test: an accrued balance must be zeroed before the transfer that pays it; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under sharePercent is set so most of the split goes to the referrer, then assert `codeOwners[_code]` and `userInfos[account].myCode` end identical in both runs.
