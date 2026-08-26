# Q3256: WombatStaking.deposit - deposit credits a balance delta as the receipt mint

## Question
wombat/WombatStaking.sol: deposit() mints lpReceived = balanceOf(lpAddress) after minus before to msg.sender, so LP tokens that reach WombatStaking for any other reason during that window are converted into receipt tokens for the depositing helper. With _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper under attacker control and the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, can an unprivileged caller sequence `deposit(address,uint256,uint256,address,address) via a pool helper` so that `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` no longer reconcile, violating the invariant that receipt tokens minted must correspond exactly to LP the depositor supplied and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: deposit credits a balance delta as the receipt mint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: deposit() mints lpReceived = balanceOf(lpAddress) after minus before to msg.sender, so LP tokens that reach WombatStaking for any other reason during that window are converted into receipt tokens for the depositing helper. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: receipt tokens minted must correspond exactly to LP the depositor supplied; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(address,uint256,uint256,address,address) via a pool helper` sequence atomically under the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, asserting at the end that `womRewards measured by balance delta` still equals `the amount queued into poolInfo.rewarder` and the PoC's balance delta is non-positive.
