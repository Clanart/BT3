# Q0174: ReferralStorage.claimReward - claimReward transfers before zeroing the balance

## Question
In rewards/ReferralStorage.sol, claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Can an unprivileged attacker reach this through `claimReward()` while the attacker controls two addresses and binds one to the other's code, and drive `refererPercentage + refereePercentage` out of agreement with `DENOMINATOR` - breaking the invariant that an accrued balance must be zeroed before the transfer that pays it - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: claimReward transfers before zeroing the balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Precondition: the attacker controls two addresses and binds one to the other's code.
- Invariant to test: an accrued balance must be zeroed before the transfer that pays it; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker controls two addresses and binds one to the other's code, snapshot `refererPercentage + refereePercentage` and `DENOMINATOR`, run the attacker's `claimReward()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
