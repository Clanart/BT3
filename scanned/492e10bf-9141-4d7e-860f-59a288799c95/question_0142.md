# Q0142: BribeRewardPool.stakeFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
Note that in rewards/BribeRewardPool.sol, BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Can an attacker holding only tokens bought on market reach it via `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` under a large bribe for the gauge is pending and no cast has run yet and force `rewards[_rewardToken].queuedRewards` apart from `totalSupply at the moment of the flush`, breaking the invariant that all reward math in a contract must read the balance ledger the contract actually maintains for Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large bribe for the gauge is pending and no cast has run yet, then assert `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` end identical in both runs.
