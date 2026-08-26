# Q3696: ReferralStorage.updateTotalFactor - cancelUnlock raises the real lock while the stored factor stays low

## Question
In rewards/ReferralStorage.sol, cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. Can an unprivileged attacker reach this through `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` while the attacker calls multiclaimFor on a set of referred accounts in one block, and drive `userInfos[account].factor` out of agreement with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` - breaking the invariant that totalBoostFactor must equal the sum of current per-user factors at all times - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: cancelUnlock raises the real lock while the stored factor stays low)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: totalBoostFactor must equal the sum of current per-user factors at all times; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker calls multiclaimFor on a set of referred accounts in one block, have the attacker run `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, then assert the victim's claimable value and the `userInfos[account].factor` versus `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` relation are unchanged by the attacker's transaction.
