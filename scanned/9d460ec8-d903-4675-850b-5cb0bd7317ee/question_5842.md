# Q5842: MasterMagpie.deposit - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol - massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Can an unprivileged attacker controlling _stakingToken, _amount, and the ERC20 the pool was registered with, under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, exploit this through `deposit(address _stakingToken, uint256 _amount)` to break the reconciliation between `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)` and the invariant that no external actor may choose the accrual checkpoints that price other users' deposits, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, have the attacker run `deposit(address _stakingToken, uint256 _amount)`, then assert the victim's claimable value and the `userInfo[_stakingToken][user].amount` versus `_calLpSupply(_stakingToken)` relation are unchanged by the attacker's transaction.
