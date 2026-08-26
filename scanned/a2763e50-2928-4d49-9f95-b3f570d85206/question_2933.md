# Q2933: WomUp.stake - stake and withdraw inside one block capture an interval

## Question
In wombat/WomUp.sol, stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Starting from a state where the attacker stakes one wei so _totalSupply is non-zero but every division truncates, can an unprivileged EOA use `stake(uint256 _amount)` to leave `rewards[account]` inconsistent with `IERC20(mgp).balanceOf(address(this))`, violating the invariant that reward share must require the stake to have been held across the interval it is paid for and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the attacker stakes one wei so _totalSupply is non-zero but every division truncates.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker stakes one wei so _totalSupply is non-zero but every division truncates, snapshot `rewards[account]` and `IERC20(mgp).balanceOf(address(this))`, run the attacker's `stake(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
