# Q4084: BribeRewardPool.stakeFor - scaling factor taken from an unrelated staking token

## Question
Consider rewards/BribeRewardPool.sol, where the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Assuming the stakingToken fixed at construction has different decimals from vlMGP, can an unprivileged attacker turn this into a divergence between `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` via `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, breaking the invariant that the scaling factor must match the unit the balance ledger is denominated in and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the stakingToken fixed at construction has different decimals from vlMGP.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` sequence atomically under the stakingToken fixed at construction has different decimals from vlMGP, asserting at the end that `totalSupply` still equals `the sum of userVotedForPoolInVlmgp over all voters for this pool` and the PoC's balance delta is non-positive.
