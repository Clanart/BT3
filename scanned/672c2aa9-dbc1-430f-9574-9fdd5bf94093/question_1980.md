# Q1980: BribeRewardPool.withdrawFor - scaling factor taken from an unrelated staking token

## Question
In rewards/BribeRewardPool.sol, the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Starting from a state where the bribe token registered for this gauge charges a transfer fee, can an unprivileged EOA use `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` to leave `totalSupply` inconsistent with `the sum of userVotedForPoolInVlmgp over all voters for this pool`, violating the invariant that the scaling factor must match the unit the balance ledger is denominated in and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the bribe token registered for this gauge charges a transfer fee, call `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, and assert `totalSupply` equals `the sum of userVotedForPoolInVlmgp over all voters for this pool` and that no account can withdraw more than it put in.
