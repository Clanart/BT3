# Q4379: WombatStaking.convertWOM - convertWOM front-runs the mWOM mint accounting

## Question
wombat/WombatStaking.sol - mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Can an unprivileged attacker controlling _amount, with no upper bound and no relation to who supplied the WOM, under the deposit token for the pool is wBNB and the helper arrived through depositNative, exploit this through `convertWOM(uint256 _amount)` to break the reconciliation between `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token` and the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the deposit token for the pool is wBNB and the helper arrived through depositNative, have the attacker run `convertWOM(uint256 _amount)`, then assert the victim's claimable value and the `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` versus `_liquidity burned from the receipt token` relation are unchanged by the attacker's transaction.
