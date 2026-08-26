# Q2979: WombatStaking.convertWOM - convertWOM is permissionless and spends the contract's own WOM

## Question
Consider wombat/WombatStaking.sol, where convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Assuming the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, can an unprivileged attacker turn this into a divergence between `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` via `convertWOM(uint256 _amount)`, breaking the invariant that committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertWOM is permissionless and spends the contract's own WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, snapshot `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM`, run the attacker's `convertWOM(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
