# Q3540: VLMGP.lockFor - dust lockFor used to pin a victim's accrual checkpoint

## Question
VLMGP.sol: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, is there an unprivileged sequence of `lockFor(uint256 _amount, address _for)` that leaves `getUserAmountInCoolDown(user)` unreconciled with `totalAmountInCoolDown`, violates the invariant that a third party must not be able to force a settlement checkpoint on another user's position, and delivers High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: dust lockFor used to pin a victim's accrual checkpoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: a third party must not be able to force a settlement checkpoint on another user's position; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, call `lockFor(uint256 _amount, address _for)`, and assert `getUserAmountInCoolDown(user)` equals `totalAmountInCoolDown` and that no account can withdraw more than it put in.
