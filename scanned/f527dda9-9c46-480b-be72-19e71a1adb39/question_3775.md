# Q3775: BribeRewardPool.stakeFor - scaling factor taken from an unrelated staking token

## Question
In rewards/BribeRewardPool.sol, the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Does `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` let an unprivileged caller exploit that under the victim has a large unsettled bribe balance, so that `_balances[account]` diverges from `totalSupply`, the invariant that the scaling factor must match the unit the balance ledger is denominated in is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`: constrain the setup so that the victim has a large unsettled bribe balance, fuzz the attacker inputs (the delta and the beneficiary, both chosen by the voter calling vote), and assert after every call that the scaling factor must match the unit the balance ledger is denominated in.
