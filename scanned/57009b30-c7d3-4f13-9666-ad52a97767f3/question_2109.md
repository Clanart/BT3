# Q2109: WombatStaking.deposit - receipt tokens minted to the helper rather than to _for

## Question
wombat/WombatStaking.sol: both deposit() and depositLP() call IMintableERC20(poolInfo.receiptToken).mint(msg.sender, ...) where msg.sender is the pool helper, and the helper then decides who to credit in MasterMagpie, so the mint and the credit are two independent decisions. Under a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, is there an unprivileged sequence of `deposit(address,uint256,uint256,address,address) via a pool helper` that leaves `feeInfos[i].value` unreconciled with `totalFee`, violates the invariant that the receipt-token mint and the MasterMagpie credit must be a single atomic attribution to one owner, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: receipt tokens minted to the helper rather than to _for)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: both deposit() and depositLP() call IMintableERC20(poolInfo.receiptToken).mint(msg.sender, ...) where msg.sender is the pool helper, and the helper then decides who to credit in MasterMagpie, so the mint and the credit are two independent decisions. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: the receipt-token mint and the MasterMagpie credit must be a single atomic attribution to one owner; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `deposit(address,uint256,uint256,address,address) via a pool helper` sequence atomically under a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, asserting at the end that `feeInfos[i].value` still equals `totalFee` and the PoC's balance delta is non-positive.
