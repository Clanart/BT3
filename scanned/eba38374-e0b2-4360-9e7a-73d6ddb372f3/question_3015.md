# Q3015: WombatStaking.convertWOM - convertWOM front-runs the mWOM mint accounting

## Question
In wombat/WombatStaking.sol, mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Can an unprivileged attacker reach this through `convertWOM(uint256 _amount)` while the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, and drive `womRewards measured by balance delta` out of agreement with `the amount queued into poolInfo.rewarder` - breaking the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, then assert `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` end identical in both runs.
