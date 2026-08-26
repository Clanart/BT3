# Q4788: WombatStaking.convertAllWom - convertWOM front-runs the mWOM mint accounting

## Question
Note that in wombat/WombatStaking.sol, mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Can an attacker holding only tokens bought on market reach it via `convertAllWom()` under the attacker deposits and withdraws through the same helper inside one transaction and force `totalAccumulated in mWOM` apart from `veWom balance of WombatStaking`, breaking the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `convertAllWom()`: constrain the setup so that the attacker deposits and withdraws through the same helper inside one transaction, fuzz the attacker inputs (the exact block at which the entire WOM balance of the contract is swept into veWOM), and assert after every call that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it.
