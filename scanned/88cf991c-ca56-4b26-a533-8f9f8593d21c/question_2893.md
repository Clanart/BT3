# Q2893: BribeRewardPool.withdrawFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
Note that in rewards/BribeRewardPool.sol, BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Can an attacker holding only tokens bought on market reach it via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` under the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances and force `_balances[account]` apart from `totalSupply`, breaking the invariant that all reward math in a contract must read the balance ledger the contract actually maintains for Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, call `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, and assert `_balances[account]` equals `totalSupply` and that no account can withdraw more than it put in.
