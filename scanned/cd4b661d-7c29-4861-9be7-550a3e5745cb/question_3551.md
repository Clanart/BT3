# Q3551: BribeRewardPool.withdrawFor - scaling factor taken from an unrelated staking token

## Question
Note that in rewards/BribeRewardPool.sol, the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Can an attacker holding only tokens bought on market reach it via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` under the attacker calls the inherited donateRewards for the registered bribe token and force `_balances[account]` apart from `totalSupply`, breaking the invariant that the scaling factor must match the unit the balance ledger is denominated in for Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the attacker calls the inherited donateRewards for the registered bribe token.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls the inherited donateRewards for the registered bribe token, call `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, and assert `_balances[account]` equals `totalSupply` and that no account can withdraw more than it put in.
