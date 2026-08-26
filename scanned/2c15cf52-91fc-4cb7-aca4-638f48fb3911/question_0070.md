# Q0070: WombatStaking.convertWOM - convertWOM front-runs the mWOM mint accounting

## Question
wombat/WombatStaking.sol: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, is there an unprivileged sequence of `convertWOM(uint256 _amount)` that leaves `IMintableERC20(poolInfo.receiptToken).totalSupply()` unreconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`, violates the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, call `convertWOM(uint256 _amount)`, and assert `IMintableERC20(poolInfo.receiptToken).totalSupply()` equals `IMasterWombat(masterWombat) staked balance for poolInfo.pid` and that no account can withdraw more than it put in.
