# Q3248: ReferralStorage.claimReward - claimReward transfers before zeroing the balance

## Question
Consider rewards/ReferralStorage.sol, where claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Assuming the referee has a large pending MGP claim in MasterMagpie, can an unprivileged attacker turn this into a divergence between `userInfos[account].rewardAmount` and `MGP.balanceOf(address(this))` via `claimReward()`, breaking the invariant that an accrued balance must be zeroed before the transfer that pays it and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: claimReward transfers before zeroing the balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: claimReward() calls MGP.safeTransfer(msg.sender, userInfo.rewardAmount), emits, and only then sets userInfo.rewardAmount = 0, with no nonReentrant modifier on the function. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: an accrued balance must be zeroed before the transfer that pays it; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the referee has a large pending MGP claim in MasterMagpie, call `claimReward()`, and assert `userInfos[account].rewardAmount` equals `MGP.balanceOf(address(this))` and that no account can withdraw more than it put in.
