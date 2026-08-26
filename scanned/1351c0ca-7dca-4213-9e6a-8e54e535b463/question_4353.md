# Q4353: WombatStaking.convertWOM - convertWOM is permissionless and spends the contract's own WOM

## Question
In wombat/WombatStaking.sol, convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Can an unprivileged attacker reach this through `convertWOM(uint256 _amount)` while the deposit token for the pool is wBNB and the helper arrived through depositNative, and drive `isPoolFeeFree[_lpToken]` out of agreement with `feeInfos.length` - breaking the invariant that committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertWOM is permissionless and spends the contract's own WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the deposit token for the pool is wBNB and the helper arrived through depositNative, have the attacker run `convertWOM(uint256 _amount)`, then assert the victim's claimable value and the `isPoolFeeFree[_lpToken]` versus `feeInfos.length` relation are unchanged by the attacker's transaction.
