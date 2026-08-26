# Q3903: BribeRewardPool.withdrawFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
In rewards/BribeRewardPool.sol, BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Can an unprivileged attacker reach this through `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` while the victim has a large unsettled bribe balance, and drive `userRewards[_rewardToken][account]` out of agreement with `earned(account,_rewardToken)` - breaking the invariant that all reward math in a contract must read the balance ledger the contract actually maintains - for Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the negative delta and whether the claim leg runs) under the victim has a large unsettled bribe balance, asserting on every row that all reward math in a contract must read the balance ledger the contract actually maintains.
