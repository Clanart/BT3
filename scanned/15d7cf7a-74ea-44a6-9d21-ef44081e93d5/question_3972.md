# Q3972: WombatStaking.convertWOM - convertWOM front-runs the mWOM mint accounting

## Question
In wombat/WombatStaking.sol, mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Does `convertWOM(uint256 _amount)` let an unprivileged caller exploit that under several feeInfos entries are active at once and the harvested amount is small, so that `IERC20(poolInfo.lpAddress).balanceOf(address(this))` diverges from `lpReceived credited by IMintableERC20(receiptToken).mint`, the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under several feeInfos entries are active at once and the harvested amount is small, then assert `IERC20(poolInfo.lpAddress).balanceOf(address(this))` and `lpReceived credited by IMintableERC20(receiptToken).mint` end identical in both runs.
