# Q2026: BribeRewardPool.withdrawFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
In rewards/BribeRewardPool.sol, BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Starting from a state where the bribe token registered for this gauge charges a transfer fee, can an unprivileged EOA use `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` to leave `userRewards[_rewardToken][account]` inconsistent with `earned(account,_rewardToken)`, violating the invariant that all reward math in a contract must read the balance ledger the contract actually maintains and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the bribe token registered for this gauge charges a transfer fee, snapshot `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)`, run the attacker's `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
