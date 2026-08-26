# Q0762: BribeRewardPool.stakeFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
Consider rewards/BribeRewardPool.sol, where BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Assuming the attacker votes and casts inside one transaction through voteAndCast, can an unprivileged attacker turn this into a divergence between `_balances[account]` and `totalSupply` via `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, breaking the invariant that all reward math in a contract must read the balance ledger the contract actually maintains and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the attacker votes and casts inside one transaction through voteAndCast.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker votes and casts inside one transaction through voteAndCast, call `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, and assert `_balances[account]` equals `totalSupply` and that no account can withdraw more than it put in.
