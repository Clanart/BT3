# Q1958: ReferralStorage.updateTotalFactor - cancelUnlock raises the real lock while the stored factor stays low

## Question
Note that in rewards/ReferralStorage.sol, cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. Can an attacker holding only tokens bought on market reach it via `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` under the contract's MGP balance is smaller than the sum of all accrued rewardAmount values and force `tiers[tierId].rewardPercentage + _calBoosted(referer)` apart from `DENOMINATOR`, breaking the invariant that totalBoostFactor must equal the sum of current per-user factors at all times for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: cancelUnlock raises the real lock while the stored factor stays low)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. Precondition: the contract's MGP balance is smaller than the sum of all accrued rewardAmount values.
- Invariant to test: totalBoostFactor must equal the sum of current per-user factors at all times; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, have the attacker run `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, then assert the victim's claimable value and the `tiers[tierId].rewardPercentage + _calBoosted(referer)` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
