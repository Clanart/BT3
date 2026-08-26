# Q2178: WombatStaking.deposit - bonus reward before-balances captured before an attacker-timed transfer

## Question
Consider wombat/WombatStaking.sol, where _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Assuming a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, can an unprivileged attacker turn this into a divergence between `totalAccumulated in mWOM` and `veWom balance of WombatStaking` via `deposit(address,uint256,uint256,address,address) via a pool helper`, breaking the invariant that harvest accounting must not credit tokens that were not produced by the harvest and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `deposit(address,uint256,uint256,address,address) via a pool helper`: constrain the setup so that a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, fuzz the attacker inputs (_amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper), and assert after every call that harvest accounting must not credit tokens that were not produced by the harvest.
