# Q0732: ReferralStorage.claimReward - claimReward transfers before zeroing the balance

## Question
Note that in rewards/ReferralStorage.sol, claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Can an attacker holding only tokens bought on market reach it via `claimReward()` under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor and force `tiers[tierId].rewardPercentage + _calBoosted(referer)` apart from `DENOMINATOR`, breaking the invariant that an accrued balance must be zeroed before the transfer that pays it for Critical - Direct theft of user funds?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: claimReward transfers before zeroing the balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: an accrued balance must be zeroed before the transfer that pays it; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the moment the accrued MGP is drawn from the shared contract balance) under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, asserting on every row that an accrued balance must be zeroed before the transfer that pays it.
