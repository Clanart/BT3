# Q3184: VLMGP.lockFor - dust lockFor used to pin a victim's accrual checkpoint

## Question
In VLMGP.sol, because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Starting from a state where the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, can an unprivileged EOA use `lockFor(uint256 _amount, address _for)` to leave `getUserTotalLocked(user)` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`, violating the invariant that a third party must not be able to force a settlement checkpoint on another user's position and extracting High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: dust lockFor used to pin a victim's accrual checkpoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Precondition: the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard.
- Invariant to test: a third party must not be able to force a settlement checkpoint on another user's position; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, call `lockFor(uint256 _amount, address _for)`, and assert `getUserTotalLocked(user)` equals `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` and that no account can withdraw more than it put in.
