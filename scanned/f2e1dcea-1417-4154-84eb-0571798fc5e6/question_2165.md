# Q2165: ReferralStorage.claimReward - claimReward transfers before zeroing the balance

## Question
rewards/ReferralStorage.sol: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Under the attacker locked vlMGP before registering a code, is there an unprivileged sequence of `claimReward()` that leaves `BoostPoint` unreconciled with `totalBoostFactor`, violates the invariant that an accrued balance must be zeroed before the transfer that pays it, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: claimReward transfers before zeroing the balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: an accrued balance must be zeroed before the transfer that pays it; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker locked vlMGP before registering a code, snapshot `BoostPoint` and `totalBoostFactor`, run the attacker's `claimReward()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
