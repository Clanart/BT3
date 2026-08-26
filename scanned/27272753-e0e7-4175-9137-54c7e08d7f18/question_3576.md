# Q3576: WombatStaking.convertAllWom - convertWOM front-runs the mWOM mint accounting

## Question
Consider wombat/WombatStaking.sol, where mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Assuming the pool is marked isPoolFeeFree so the fee loop is skipped entirely, can an unprivileged attacker turn this into a divergence between `IERC20(poolInfo.lpAddress).balanceOf(address(this))` and `lpReceived credited by IMintableERC20(receiptToken).mint` via `convertAllWom()`, breaking the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the pool is marked isPoolFeeFree so the fee loop is skipped entirely, have the attacker run `convertAllWom()`, then assert the victim's claimable value and the `IERC20(poolInfo.lpAddress).balanceOf(address(this))` versus `lpReceived credited by IMintableERC20(receiptToken).mint` relation are unchanged by the attacker's transaction.
