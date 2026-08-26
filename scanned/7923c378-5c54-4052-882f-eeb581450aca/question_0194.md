# Q0194: WombatStaking.convertAllWom - convertWOM front-runs the mWOM mint accounting

## Question
wombat/WombatStaking.sol - mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Can an unprivileged attacker controlling the exact block at which the entire WOM balance of the contract is swept into veWOM, under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, exploit this through `convertAllWom()` to break the reconciliation between `totalAccumulated in mWOM` and `veWom balance of WombatStaking` and the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the entire WOM balance of the contract is swept into veWOM) under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, asserting on every row that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it.
