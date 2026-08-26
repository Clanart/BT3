# Q1067: AnkrBNBPoolHelper.deposit - deposit and withdraw both run the full harvest and fee path

## Question
Consider wombat/AnkrBNBPoolHelper.sol, where WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Assuming the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, can an unprivileged attacker turn this into a divergence between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` via `deposit(uint256 _amount, uint256 _minimumLiquidity)`, breaking the invariant that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: deposit and withdraw both run the full harvest and fee path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount, uint256 _minimumLiquidity)` sequence atomically under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, asserting at the end that `IERC20(stakingToken).totalSupply()` still equals `the MasterWombat staked balance for pid` and the PoC's balance delta is non-positive.
