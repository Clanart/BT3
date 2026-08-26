# Q1749: ReferralStorage.claimReward - claimReward transfers before zeroing the balance

## Question
rewards/ReferralStorage.sol: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Under the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, is there an unprivileged sequence of `claimReward()` that leaves `myReferer[account]` unreconciled with `userInfos[account].codeIUsed`, violates the invariant that an accrued balance must be zeroed before the transfer that pays it, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: claimReward transfers before zeroing the balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Precondition: the contract's MGP balance is smaller than the sum of all accrued rewardAmount values.
- Invariant to test: an accrued balance must be zeroed before the transfer that pays it; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, then assert `myReferer[account]` and `userInfos[account].codeIUsed` end identical in both runs.
