# Q2353: WomUp.stake - stake and withdraw inside one block capture an interval

## Question
wombat/WomUp.sol: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Under the MGP balance is below the sum of accrued rewards, is there an unprivileged sequence of `stake(uint256 _amount)` that leaves `_totalSupply` unreconciled with `IERC20(mWom).balanceOf(address(this))`, violates the invariant that reward share must require the stake to have been held across the interval it is paid for, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the MGP balance is below the sum of accrued rewards.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and the block, with the WOM immediately converted 1:1 into mWOM) under the MGP balance is below the sum of accrued rewards, asserting on every row that reward share must require the stake to have been held across the interval it is paid for.
