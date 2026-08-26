# Q3468: BribeRewardPool.stakeFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
rewards/BribeRewardPool.sol - BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Can an unprivileged attacker controlling the delta and the beneficiary, both chosen by the voter calling vote, under the attacker calls the inherited donateRewards for the registered bribe token, exploit this through `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` to break the reconciliation between `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` and the invariant that all reward math in a contract must read the balance ledger the contract actually maintains, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the attacker calls the inherited donateRewards for the registered bribe token.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` sequence atomically under the attacker calls the inherited donateRewards for the registered bribe token, asserting at the end that `totalSupply` still equals `the sum of userVotedForPoolInVlmgp over all voters for this pool` and the PoC's balance delta is non-positive.
