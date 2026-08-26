# Q3552: ReferralStorage.claimReward - claimReward transfers before zeroing the balance

## Question
Consider rewards/ReferralStorage.sol, where claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Assuming the attacker calls multiclaimFor on a set of referred accounts in one block, can an unprivileged attacker turn this into a divergence between `refererPercentage + refereePercentage` and `DENOMINATOR` via `claimReward()`, breaking the invariant that an accrued balance must be zeroed before the transfer that pays it and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: claimReward transfers before zeroing the balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: an accrued balance must be zeroed before the transfer that pays it; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker calls multiclaimFor on a set of referred accounts in one block, have the attacker run `claimReward()`, then assert the victim's claimable value and the `refererPercentage + refereePercentage` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
