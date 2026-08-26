# Q3840: ReferralStorage.claimReward - claimReward transfers before zeroing the balance

## Question
Consider rewards/ReferralStorage.sol, where claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Assuming sharePercent is set so most of the split goes to the referee, can an unprivileged attacker turn this into a divergence between `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR` via `claimReward()`, breaking the invariant that an accrued balance must be zeroed before the transfer that pays it and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: claimReward transfers before zeroing the balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Precondition: sharePercent is set so most of the split goes to the referee.
- Invariant to test: an accrued balance must be zeroed before the transfer that pays it; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up sharePercent is set so most of the split goes to the referee, snapshot `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR`, run the attacker's `claimReward()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
