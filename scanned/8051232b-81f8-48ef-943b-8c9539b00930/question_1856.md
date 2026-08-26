# Q1856: WombatStaking.convertAllWom - convertWOM front-runs the mWOM mint accounting

## Question
Consider wombat/WombatStaking.sol, where mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Assuming a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, can an unprivileged attacker turn this into a divergence between `feeInfos[i].value` and `totalFee` via `convertAllWom()`, breaking the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `convertAllWom()` sequence atomically under a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, asserting at the end that `feeInfos[i].value` still equals `totalFee` and the PoC's balance delta is non-positive.
