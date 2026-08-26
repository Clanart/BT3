# Q2809: WombatStaking.deposit - bonus reward before-balances captured before an attacker-timed transfer

## Question
wombat/WombatStaking.sol: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. With _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper under attacker control and smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, can an unprivileged caller sequence `deposit(address,uint256,uint256,address,address) via a pool helper` so that `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` no longer reconcile, violating the invariant that harvest accounting must not credit tokens that were not produced by the harvest and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `deposit(address,uint256,uint256,address,address) via a pool helper` sequence atomically under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, asserting at the end that `IERC20(wom).balanceOf(address(this))` still equals `totalConverted in mWOM` and the PoC's balance delta is non-positive.
