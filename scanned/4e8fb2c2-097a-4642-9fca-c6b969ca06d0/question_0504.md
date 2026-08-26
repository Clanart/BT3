# Q0504: WombatStaking.deposit - deposit credits a balance delta as the receipt mint

## Question
wombat/WombatStaking.sol: deposit() mints lpReceived = balanceOf(lpAddress) after minus before to msg.sender, so LP tokens that reach WombatStaking for any other reason during that window are converted into receipt tokens for the depositing helper. With _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper under attacker control and the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, can an unprivileged caller sequence `deposit(address,uint256,uint256,address,address) via a pool helper` so that `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` no longer reconcile, violating the invariant that receipt tokens minted must correspond exactly to LP the depositor supplied and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: deposit credits a balance delta as the receipt mint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: deposit() mints lpReceived = balanceOf(lpAddress) after minus before to msg.sender, so LP tokens that reach WombatStaking for any other reason during that window are converted into receipt tokens for the depositing helper. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: receipt tokens minted must correspond exactly to LP the depositor supplied; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper) under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, asserting on every row that receipt tokens minted must correspond exactly to LP the depositor supplied.
