# Q2751: WombatStaking.deposit - receipt tokens minted to the helper rather than to _for

## Question
wombat/WombatStaking.sol: both deposit() and depositLP() call IMintableERC20(poolInfo.receiptToken).mint(msg.sender, ...) where msg.sender is the pool helper, and the helper then decides who to credit in MasterMagpie, so the mint and the credit are two independent decisions. With _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper under attacker control and smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, can an unprivileged caller sequence `deposit(address,uint256,uint256,address,address) via a pool helper` so that `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` no longer reconcile, violating the invariant that the receipt-token mint and the MasterMagpie credit must be a single atomic attribution to one owner and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: receipt tokens minted to the helper rather than to _for)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: both deposit() and depositLP() call IMintableERC20(poolInfo.receiptToken).mint(msg.sender, ...) where msg.sender is the pool helper, and the helper then decides who to credit in MasterMagpie, so the mint and the credit are two independent decisions. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: the receipt-token mint and the MasterMagpie credit must be a single atomic attribution to one owner; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, then assert `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` end identical in both runs.
