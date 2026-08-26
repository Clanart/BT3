# Q1379: WombatStaking.deposit - deposit credits a balance delta as the receipt mint

## Question
wombat/WombatStaking.sol - deposit() mints lpReceived = balanceOf(lpAddress) after minus before to msg.sender, so LP tokens that reach WombatStaking for any other reason during that window are converted into receipt tokens for the depositing helper. Can an unprivileged attacker controlling _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper, under the contract is holding WOM collected as a protocol fee that has not yet been split, exploit this through `deposit(address,uint256,uint256,address,address) via a pool helper` to break the reconciliation between `totalAccumulated in mWOM` and `veWom balance of WombatStaking` and the invariant that receipt tokens minted must correspond exactly to LP the depositor supplied, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: deposit credits a balance delta as the receipt mint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: deposit() mints lpReceived = balanceOf(lpAddress) after minus before to msg.sender, so LP tokens that reach WombatStaking for any other reason during that window are converted into receipt tokens for the depositing helper. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: receipt tokens minted must correspond exactly to LP the depositor supplied; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the contract is holding WOM collected as a protocol fee that has not yet been split, snapshot `totalAccumulated in mWOM` and `veWom balance of WombatStaking`, run the attacker's `deposit(address,uint256,uint256,address,address) via a pool helper` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
