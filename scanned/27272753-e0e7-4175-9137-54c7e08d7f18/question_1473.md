# Q1473: WomUp.withdraw - stake and withdraw inside one block capture an interval

## Question
In wombat/WomUp.sol, stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Can an unprivileged attacker reach this through `withdraw(uint256 amount, bool claim)` while the reward period has just ended so periodFinish is behind block.timestamp, and drive `rewardRate * duration` out of agreement with `IERC20(mgp).balanceOf(address(this))` - breaking the invariant that reward share must require the stake to have been held across the interval it is paid for - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the reward period has just ended so periodFinish is behind block.timestamp.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the reward period has just ended so periodFinish is behind block.timestamp, have the attacker run `withdraw(uint256 amount, bool claim)`, then assert the victim's claimable value and the `rewardRate * duration` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
