# Q4182: WombatStaking.deposit - deposit credits a balance delta as the receipt mint

## Question
wombat/WombatStaking.sol: deposit() mints lpReceived = balanceOf(lpAddress) after minus before to msg.sender, so LP tokens that reach WombatStaking for any other reason during that window are converted into receipt tokens for the depositing helper. Under several feeInfos entries are active at once and the harvested amount is small, is there an unprivileged sequence of `deposit(address,uint256,uint256,address,address) via a pool helper` that leaves `IERC20(poolInfo.lpAddress).balanceOf(address(this))` unreconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`, violates the invariant that receipt tokens minted must correspond exactly to LP the depositor supplied, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: deposit credits a balance delta as the receipt mint)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: deposit() mints lpReceived = balanceOf(lpAddress) after minus before to msg.sender, so LP tokens that reach WombatStaking for any other reason during that window are converted into receipt tokens for the depositing helper. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: receipt tokens minted must correspond exactly to LP the depositor supplied; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange several feeInfos entries are active at once and the harvested amount is small, call `deposit(address,uint256,uint256,address,address) via a pool helper`, and assert `IERC20(poolInfo.lpAddress).balanceOf(address(this))` equals `lpReceived credited by IMintableERC20(receiptToken).mint` and that no account can withdraw more than it put in.
