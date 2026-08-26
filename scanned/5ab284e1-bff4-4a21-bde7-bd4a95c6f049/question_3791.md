# Q3791: BribeRewardPool.stakeFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
rewards/BribeRewardPool.sol: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Under the victim has a large unsettled bribe balance, is there an unprivileged sequence of `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` that leaves `rewards[_rewardToken].rewardPerTokenStored` unreconciled with `userRewardPerTokenPaid[_rewardToken][account]`, violates the invariant that all reward math in a contract must read the balance ledger the contract actually maintains, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the delta and the beneficiary, both chosen by the voter calling vote) under the victim has a large unsettled bribe balance, asserting on every row that all reward math in a contract must read the balance ledger the contract actually maintains.
