# Q4572: WombatStaking.deposit - receipt tokens minted to the helper rather than to _for

## Question
Note that in wombat/WombatStaking.sol, both deposit() and depositLP() call IMintableERC20(poolInfo.receiptToken).mint(msg.sender, ...) where msg.sender is the pool helper, and the helper then decides who to credit in MasterMagpie, so the mint and the credit are two independent decisions. Can an attacker holding only tokens bought on market reach it via `deposit(address,uint256,uint256,address,address) via a pool helper` under the deposit token for the pool is wBNB and the helper arrived through depositNative and force `IMintableERC20(poolInfo.receiptToken).totalSupply()` apart from `IMasterWombat(masterWombat) staked balance for poolInfo.pid`, breaking the invariant that the receipt-token mint and the MasterMagpie credit must be a single atomic attribution to one owner for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: receipt tokens minted to the helper rather than to _for)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: both deposit() and depositLP() call IMintableERC20(poolInfo.receiptToken).mint(msg.sender, ...) where msg.sender is the pool helper, and the helper then decides who to credit in MasterMagpie, so the mint and the credit are two independent decisions. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: the receipt-token mint and the MasterMagpie credit must be a single atomic attribution to one owner; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the deposit token for the pool is wBNB and the helper arrived through depositNative, call `deposit(address,uint256,uint256,address,address) via a pool helper`, and assert `IMintableERC20(poolInfo.receiptToken).totalSupply()` equals `IMasterWombat(masterWombat) staked balance for poolInfo.pid` and that no account can withdraw more than it put in.
