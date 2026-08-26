# Q4024: BribeRewardPool.updateFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
In rewards/BribeRewardPool.sol, BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Starting from a state where the victim has a large unsettled bribe balance, can an unprivileged EOA use `updateFor(address _account) inherited from BaseRewardPoolV2` to leave `_balances[account]` inconsistent with `totalSupply`, violating the invariant that all reward math in a contract must read the balance ledger the contract actually maintains and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unsettled bribe balance, call `updateFor(address _account) inherited from BaseRewardPoolV2`, and assert `_balances[account]` equals `totalSupply` and that no account can withdraw more than it put in.
