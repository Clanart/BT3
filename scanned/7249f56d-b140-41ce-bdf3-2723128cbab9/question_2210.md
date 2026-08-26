# Q2210: BribeRewardPool.updateFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
In rewards/BribeRewardPool.sol, BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Does `updateFor(address _account) inherited from BaseRewardPoolV2` let an unprivileged caller exploit that under the bribe token registered for this gauge charges a transfer fee, so that `_balances[account]` diverges from `totalSupply`, the invariant that all reward math in a contract must read the balance ledger the contract actually maintains is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `updateFor(address _account) inherited from BaseRewardPoolV2`: constrain the setup so that the bribe token registered for this gauge charges a transfer fee, fuzz the attacker inputs (the victim address and the block at which their bribe index is pinned), and assert after every call that all reward math in a contract must read the balance ledger the contract actually maintains.
