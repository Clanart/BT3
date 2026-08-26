# Q4608: WombatStaking.deposit - bonus reward before-balances captured before an attacker-timed transfer

## Question
In wombat/WombatStaking.sol, _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Can an unprivileged attacker reach this through `deposit(address,uint256,uint256,address,address) via a pool helper` while the deposit token for the pool is wBNB and the helper arrived through depositNative, and drive `IERC20(poolInfo.lpAddress).balanceOf(address(this))` out of agreement with `lpReceived credited by IMintableERC20(receiptToken).mint` - breaking the invariant that harvest accounting must not credit tokens that were not produced by the harvest - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the deposit token for the pool is wBNB and the helper arrived through depositNative, call `deposit(address,uint256,uint256,address,address) via a pool helper`, and assert `IERC20(poolInfo.lpAddress).balanceOf(address(this))` equals `lpReceived credited by IMintableERC20(receiptToken).mint` and that no account can withdraw more than it put in.
