# Q2468: WomUp.withdraw - stake and withdraw inside one block capture an interval

## Question
In wombat/WomUp.sol, stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Starting from a state where the MGP balance is below the sum of accrued rewards, can an unprivileged EOA use `withdraw(uint256 amount, bool claim)` to leave `rewardPerTokenStored` inconsistent with `userRewardPerTokenPaid[account]`, violating the invariant that reward share must require the stake to have been held across the interval it is paid for and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the MGP balance is below the sum of accrued rewards.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `withdraw(uint256 amount, bool claim)`: constrain the setup so that the MGP balance is below the sum of accrued rewards, fuzz the attacker inputs (amount and whether the claim leg runs in the same call), and assert after every call that reward share must require the stake to have been held across the interval it is paid for.
