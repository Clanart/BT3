# Q2523: WombatStaking.convertAllWom - convertWOM front-runs the mWOM mint accounting

## Question
In wombat/WombatStaking.sol, mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Does `convertAllWom()` let an unprivileged caller exploit that under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, so that `womRewards measured by balance delta` diverges from `the amount queued into poolInfo.rewarder`, the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, snapshot `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder`, run the attacker's `convertAllWom()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
