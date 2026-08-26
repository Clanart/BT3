# Q1348: VLMGP.lockFor - dust lockFor used to pin a victim's accrual checkpoint

## Question
VLMGP.sol: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Under coolDownInSecs is at its configured production value and endTime is far in the future, is there an unprivileged sequence of `lockFor(uint256 _amount, address _for)` that leaves `totalPenalty` unreconciled with `IERC20(MGP).balanceOf(address(this))`, violates the invariant that a third party must not be able to force a settlement checkpoint on another user's position, and delivers High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: dust lockFor used to pin a victim's accrual checkpoint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: because _lock() routes through MasterMagpie.depositVlMGPFor, which runs _harvestMGP and _harvestBaseRewarder against the victim, a one-wei lockFor forces a full settlement of the victim's vlMGP accrual at an attacker-chosen block. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: a third party must not be able to force a settlement checkpoint on another user's position; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `lockFor(uint256 _amount, address _for)`: constrain the setup so that coolDownInSecs is at its configured production value and endTime is far in the future, fuzz the attacker inputs (_for (any victim address) and _amount, including one wei), and assert after every call that a third party must not be able to force a settlement checkpoint on another user's position.
