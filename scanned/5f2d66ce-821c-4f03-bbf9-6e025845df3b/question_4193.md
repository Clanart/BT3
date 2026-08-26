# Q4193: VLMGP.lockFor - dust lockFor used to pin a victim's accrual checkpoint

## Question
VLMGP.sol - because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Can an unprivileged attacker controlling _for (any victim address) and _amount, including one wei, under the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, exploit this through `lockFor(uint256 _amount, address _for)` to break the reconciliation between `getRewardablePercentWAD(user)` and `userUnlockings[user][i].amountInCoolDown` and the invariant that a third party must not be able to force a settlement checkpoint on another user's position, yielding High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: dust lockFor used to pin a victim's accrual checkpoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Precondition: the victim has a large unsettled userRewards balance in vlMGPBaseRewarder.
- Invariant to test: a third party must not be able to force a settlement checkpoint on another user's position; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, have the attacker run `lockFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `getRewardablePercentWAD(user)` versus `userUnlockings[user][i].amountInCoolDown` relation are unchanged by the attacker's transaction.
