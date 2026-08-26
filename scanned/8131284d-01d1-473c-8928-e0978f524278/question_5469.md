# Q5469: WombatStaking.convertWOM - convertWOM front-runs the mWOM mint accounting

## Question
Note that in wombat/WombatStaking.sol, mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Can an attacker holding only tokens bought on market reach it via `convertWOM(uint256 _amount)` under the veWOM contract leaves a non-zero allowance after mint and force `feeInfos[i].value` apart from `totalFee`, breaking the invariant that the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: convertWOM front-runs the mWOM mint accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: mWOM._convert transfers WOM to WombatStaking and then calls convertWOM(_amount), but an attacker can call convertWOM or convertAllWom in between so the veWOM minted for that WOM is attributed to a different call and mWOM.totalAccumulated no longer tracks the veWOM actually obtained. Precondition: the veWOM contract leaves a non-zero allowance after mint.
- Invariant to test: the veWOM minted for a given WOM deposit must be attributed to the mWOM mint that supplied it; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the veWOM contract leaves a non-zero allowance after mint, snapshot `feeInfos[i].value` and `totalFee`, run the attacker's `convertWOM(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
