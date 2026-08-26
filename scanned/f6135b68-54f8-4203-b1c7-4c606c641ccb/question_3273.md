# Q3273: WombatStaking.deposit - receipt tokens minted to the helper rather than to _for

## Question
In wombat/WombatStaking.sol, both deposit() and depositLP() call IMintableERC20(poolInfo.receiptToken).mint(msg.sender, ...) where msg.sender is the pool helper, and the helper then decides who to credit in MasterMagpie, so the mint and the credit are two independent decisions. Starting from a state where the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, can an unprivileged EOA use `deposit(address,uint256,uint256,address,address) via a pool helper` to leave `isPoolFeeFree[_lpToken]` inconsistent with `feeInfos.length`, violating the invariant that the receipt-token mint and the MasterMagpie credit must be a single atomic attribution to one owner and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: receipt tokens minted to the helper rather than to _for)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: both deposit() and depositLP() call IMintableERC20(poolInfo.receiptToken).mint(msg.sender, ...) where msg.sender is the pool helper, and the helper then decides who to credit in MasterMagpie, so the mint and the credit are two independent decisions. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: the receipt-token mint and the MasterMagpie credit must be a single atomic attribution to one owner; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, call `deposit(address,uint256,uint256,address,address) via a pool helper`, and assert `isPoolFeeFree[_lpToken]` equals `feeInfos.length` and that no account can withdraw more than it put in.
