# Q5457: WombatStaking.convertWOM - convertWOM is permissionless and spends the contract's own WOM

## Question
In wombat/WombatStaking.sol, convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Can an unprivileged attacker reach this through `convertWOM(uint256 _amount)` while the veWOM contract leaves a non-zero allowance after mint, and drive `totalAccumulated in mWOM` out of agreement with `veWom balance of WombatStaking` - breaking the invariant that committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertWOM is permissionless and spends the contract's own WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Precondition: the veWOM contract leaves a non-zero allowance after mint.
- Invariant to test: committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the veWOM contract leaves a non-zero allowance after mint, snapshot `totalAccumulated in mWOM` and `veWom balance of WombatStaking`, run the attacker's `convertWOM(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
