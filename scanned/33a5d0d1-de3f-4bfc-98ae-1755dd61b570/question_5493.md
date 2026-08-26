# Q5493: WombatStaking.convertAllWom - convertWOM front-runs the mWOM mint accounting

## Question
wombat/WombatStaking.sol: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Under the veWOM contract leaves a non-zero allowance after mint, is there an unprivileged sequence of `convertAllWom()` that leaves `womRewards measured by balance delta` unreconciled with `the amount queued into poolInfo.rewarder`, violates the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: the veWOM contract leaves a non-zero allowance after mint.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the veWOM contract leaves a non-zero allowance after mint, then assert `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` end identical in both runs.
