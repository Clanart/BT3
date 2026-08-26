# Q3477: WombatStaking.convertWOM - convertWOM is permissionless and spends the contract's own WOM

## Question
wombat/WombatStaking.sol - convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Can an unprivileged attacker controlling _amount, with no upper bound and no relation to who supplied the WOM, under the pool is marked isPoolFeeFree so the fee loop is skipped entirely, exploit this through `convertWOM(uint256 _amount)` to break the reconciliation between `feeInfos[i].value` and `totalFee` and the invariant that committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertWOM is permissionless and spends the contract's own WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `convertWOM(uint256 _amount)`: constrain the setup so that the pool is marked isPoolFeeFree so the fee loop is skipped entirely, fuzz the attacker inputs (_amount, with no upper bound and no relation to who supplied the WOM), and assert after every call that committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint.
