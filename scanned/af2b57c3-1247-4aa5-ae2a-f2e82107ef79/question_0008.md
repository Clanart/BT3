# Q0008: WombatStaking.convertWOM - convertWOM is permissionless and spends the contract's own WOM

## Question
wombat/WombatStaking.sol: convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, is there an unprivileged sequence of `convertWOM(uint256 _amount)` that leaves `IERC20(poolInfo.lpAddress).balanceOf(address(this))` unreconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`, violates the invariant that committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertWOM is permissionless and spends the contract's own WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, snapshot `IERC20(poolInfo.lpAddress).balanceOf(address(this))` and `lpReceived credited by IMintableERC20(receiptToken).mint`, run the attacker's `convertWOM(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
