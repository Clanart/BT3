# Q3736: WombatStaking.deposit - deposit credits a balance delta as the receipt mint

## Question
wombat/WombatStaking.sol: deposit() mints lpReceived = balanceOf(lpAddress) after minus before to msg.sender, so LP tokens that reach WombatStaking for any other reason during that window are converted into receipt tokens for the depositing helper. With _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper under attacker control and the pool is marked isPoolFeeFree so the fee loop is skipped entirely, can an unprivileged caller sequence `deposit(address,uint256,uint256,address,address) via a pool helper` so that `isPoolFeeFree[_lpToken]` and `feeInfos.length` no longer reconcile, violating the invariant that receipt tokens minted must correspond exactly to LP the depositor supplied and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: deposit credits a balance delta as the receipt mint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: deposit() mints lpReceived = balanceOf(lpAddress) after minus before to msg.sender, so LP tokens that reach WombatStaking for any other reason during that window are converted into receipt tokens for the depositing helper. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: receipt tokens minted must correspond exactly to LP the depositor supplied; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `deposit(address,uint256,uint256,address,address) via a pool helper`: constrain the setup so that the pool is marked isPoolFeeFree so the fee loop is skipped entirely, fuzz the attacker inputs (_amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper), and assert after every call that receipt tokens minted must correspond exactly to LP the depositor supplied.
