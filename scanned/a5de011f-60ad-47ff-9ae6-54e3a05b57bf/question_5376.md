# Q5376: WombatStaking.deposit - receipt tokens minted to the helper rather than to _for

## Question
Consider wombat/WombatStaking.sol, where both deposit() and depositLP() call IMintableERC20(poolInfo.receiptToken).mint(msg.sender, ...) where msg.sender is the pool helper, and the helper then decides who to credit in MasterMagpie, so the mint and the credit are two independent decisions. Assuming the bonus reward token registered for the asset is also one of the fee currencies, can an unprivileged attacker turn this into a divergence between `feeInfos[i].value` and `totalFee` via `deposit(address,uint256,uint256,address,address) via a pool helper`, breaking the invariant that the receipt-token mint and the MasterMagpie credit must be a single atomic attribution to one owner and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: receipt tokens minted to the helper rather than to _for)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: both deposit() and depositLP() call IMintableERC20(poolInfo.receiptToken).mint(msg.sender, ...) where msg.sender is the pool helper, and the helper then decides who to credit in MasterMagpie, so the mint and the credit are two independent decisions. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: the receipt-token mint and the MasterMagpie credit must be a single atomic attribution to one owner; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bonus reward token registered for the asset is also one of the fee currencies, then assert `feeInfos[i].value` and `totalFee` end identical in both runs.
