# Q2760: BribeRewardPool.stakeFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
In rewards/BribeRewardPool.sol, BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Can an unprivileged attacker reach this through `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` while the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, and drive `rewards[_rewardToken].queuedRewards` out of agreement with `totalSupply at the moment of the flush` - breaking the invariant that all reward math in a contract must read the balance ledger the contract actually maintains - for Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, call `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, and assert `rewards[_rewardToken].queuedRewards` equals `totalSupply at the moment of the flush` and that no account can withdraw more than it put in.
