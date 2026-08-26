# Q1334: BribeRewardPool.stakeFor - scaling factor taken from an unrelated staking token

## Question
rewards/BribeRewardPool.sol - the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Can an unprivileged attacker controlling the delta and the beneficiary, both chosen by the voter calling vote, under totalSupply is zero because every voter has unvoted, exploit this through `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` to break the reconciliation between `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` and the invariant that the scaling factor must match the unit the balance ledger is denominated in, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish totalSupply is zero because every voter has unvoted, have the attacker run `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, then assert the victim's claimable value and the `rewards[_rewardToken].queuedRewards` versus `totalSupply at the moment of the flush` relation are unchanged by the attacker's transaction.
