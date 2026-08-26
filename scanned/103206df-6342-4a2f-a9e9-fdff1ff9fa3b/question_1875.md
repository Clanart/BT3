# Q1875: VLMGP.lockFor - dust lockFor used to pin a victim's accrual checkpoint

## Question
Note that in VLMGP.sol, because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Can an attacker holding only tokens bought on market reach it via `lockFor(uint256 _amount, address _for)` under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one and force `userInfos[user].factor in ReferralStorage` apart from `getUserTotalLocked(user)`, breaking the invariant that a third party must not be able to force a settlement checkpoint on another user's position for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: dust lockFor used to pin a victim's accrual checkpoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Precondition: the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one.
- Invariant to test: a third party must not be able to force a settlement checkpoint on another user's position; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, have the attacker run `lockFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `userInfos[user].factor in ReferralStorage` versus `getUserTotalLocked(user)` relation are unchanged by the attacker's transaction.
