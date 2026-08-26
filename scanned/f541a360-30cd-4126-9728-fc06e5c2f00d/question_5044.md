# Q5044: WombatStaking.convertWOM - convertWOM is permissionless and spends the contract's own WOM

## Question
In wombat/WombatStaking.sol, convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Starting from a state where a large honest deposit is pending in the mempool for the same pool, can an unprivileged EOA use `convertWOM(uint256 _amount)` to leave `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` inconsistent with `_liquidity burned from the receipt token`, violating the invariant that committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertWOM is permissionless and spends the contract's own WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM(uint256) carries only whenNotPaused, has no caller restriction, and locks the contract's WOM balance into veWOM for lockDays without minting any mWOM to anyone, so any address can decide when and how much of the pooled WOM is committed. Precondition: a large honest deposit is pending in the mempool for the same pool.
- Invariant to test: committing pooled WOM into a multi-day veWOM lock must be an authorised action tied to a matching mWOM mint; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish a large honest deposit is pending in the mempool for the same pool, have the attacker run `convertWOM(uint256 _amount)`, then assert the victim's claimable value and the `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` versus `_liquidity burned from the receipt token` relation are unchanged by the attacker's transaction.
