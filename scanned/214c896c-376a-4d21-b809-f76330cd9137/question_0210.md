# Q0210: WomUp.withdraw - stake and withdraw inside one block capture an interval

## Question
wombat/WomUp.sol: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. With amount and whether the claim leg runs in the same call under attacker control and the attacker is the only staker for a single block, can an unprivileged caller sequence `withdraw(uint256 amount, bool claim)` so that `rewardPerTokenStored` and `userRewardPerTokenPaid[account]` no longer reconcile, violating the invariant that reward share must require the stake to have been held across the interval it is paid for and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the attacker is the only staker for a single block.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (amount and whether the claim leg runs in the same call) under the attacker is the only staker for a single block, asserting on every row that reward share must require the stake to have been held across the interval it is paid for.
