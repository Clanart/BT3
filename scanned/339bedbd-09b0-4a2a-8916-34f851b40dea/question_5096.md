# Q5096: WombatStaking.convertAllWom - convertWOM front-runs the mWOM mint accounting

## Question
Consider wombat/WombatStaking.sol, where mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Assuming a large honest deposit is pending in the mempool for the same pool, can an unprivileged attacker turn this into a divergence between `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` via `convertAllWom()`, breaking the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: a large honest deposit is pending in the mempool for the same pool.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large honest deposit is pending in the mempool for the same pool, then assert `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` end identical in both runs.
