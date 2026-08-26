# Q1279: ReferralStorage.claimReward - claimReward transfers before zeroing the balance

## Question
rewards/ReferralStorage.sol: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. With the moment the accrued MGP is drawn from the shared contract balance under attacker control and BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, can an unprivileged caller sequence `claimReward()` so that `codeOwners[_code]` and `userInfos[account].myCode` no longer reconcile, violating the invariant that an accrued balance must be zeroed before the transfer that pays it and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: claimReward transfers before zeroing the balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Precondition: BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR.
- Invariant to test: an accrued balance must be zeroed before the transfer that pays it; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, call `claimReward()`, and assert `codeOwners[_code]` equals `userInfos[account].myCode` and that no account can withdraw more than it put in.
