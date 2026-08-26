# Q1078: WomUp.withdraw - stake and withdraw inside one block capture an interval

## Question
In wombat/WomUp.sol, stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Does `withdraw(uint256 amount, bool claim)` let an unprivileged caller exploit that under _totalSupply exceeds the mWOM balance the contract actually holds, so that `lastUpdateTime` diverges from `periodFinish`, the invariant that reward share must require the stake to have been held across the interval it is paid for is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: _totalSupply exceeds the mWOM balance the contract actually holds.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange _totalSupply exceeds the mWOM balance the contract actually holds, call `withdraw(uint256 amount, bool claim)`, and assert `lastUpdateTime` equals `periodFinish` and that no account can withdraw more than it put in.
