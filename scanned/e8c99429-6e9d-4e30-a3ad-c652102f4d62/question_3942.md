# Q3942: WombatStaking.convertWOM - convertWOM is permissionless and spends the contract's own WOM

## Question
In wombat/WombatStaking.sol, convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Does `convertWOM(uint256 _amount)` let an unprivileged caller exploit that under several feeInfos entries are active at once and the harvested amount is small, so that `womRewards measured by balance delta` diverges from `the amount queued into poolInfo.rewarder`, the invariant that committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertWOM is permissionless and spends the contract's own WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, with no upper bound and no relation to who supplied the WOM) under several feeInfos entries are active at once and the harvested amount is small, asserting on every row that committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint.
