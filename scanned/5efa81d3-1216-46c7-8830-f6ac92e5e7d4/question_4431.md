# Q4431: WombatStaking.convertAllWom - convertWOM front-runs the mWOM mint accounting

## Question
wombat/WombatStaking.sol: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. With the exact block at which the entire WOM balance of the contract is swept into veWOM under attacker control and the deposit token for the pool is wBNB and the helper arrived through depositNative, can an unprivileged caller sequence `convertAllWom()` so that `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` no longer reconcile, violating the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertAllWom()` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAllWom()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the entire WOM balance of the contract is swept into veWOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the deposit token for the pool is wBNB and the helper arrived through depositNative, call `convertAllWom()`, and assert `IMintableERC20(poolInfo.receiptToken).totalSupply()` equals `IMasterWombat(masterWombat) staked balance for poolInfo.pid` and that no account can withdraw more than it put in.
