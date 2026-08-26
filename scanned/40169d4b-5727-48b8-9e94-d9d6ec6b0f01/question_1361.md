# Q1361: BribeRewardPool.stakeFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
rewards/BribeRewardPool.sol: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. With the delta and the beneficiary, both chosen by the voter calling vote under attacker control and totalSupply is zero because every voter has unvoted, can an unprivileged caller sequence `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` so that `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` no longer reconcile, violating the invariant that all reward math in a contract must read the balance ledger the contract actually maintains and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the delta and the beneficiary, both chosen by the voter calling vote) under totalSupply is zero because every voter has unvoted, asserting on every row that all reward math in a contract must read the balance ledger the contract actually maintains.
