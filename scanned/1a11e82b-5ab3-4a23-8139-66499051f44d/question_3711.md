# Q3711: BribeRewardPool.updateFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
Consider rewards/BribeRewardPool.sol, where BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Assuming the attacker calls the inherited donateRewards for the registered bribe token, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` via `updateFor(address _account) inherited from BaseRewardPoolV2`, breaking the invariant that all reward math in a contract must read the balance ledger the contract actually maintains and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: the attacker calls the inherited donateRewards for the registered bribe token.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `updateFor(address _account) inherited from BaseRewardPoolV2`: constrain the setup so that the attacker calls the inherited donateRewards for the registered bribe token, fuzz the attacker inputs (the victim address and the block at which their bribe index is pinned), and assert after every call that all reward math in a contract must read the balance ledger the contract actually maintains.
