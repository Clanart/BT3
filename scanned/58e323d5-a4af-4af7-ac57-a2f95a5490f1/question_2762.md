# Q2762: WomUp.withdraw - stake and withdraw inside one block capture an interval

## Question
wombat/WomUp.sol: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. With amount and whether the claim leg runs in the same call under attacker control and the attacker calls getReward immediately after a large stake by another user, can an unprivileged caller sequence `withdraw(uint256 amount, bool claim)` so that `rewards[account]` and `IERC20(mgp).balanceOf(address(this))` no longer reconcile, violating the invariant that reward share must require the stake to have been held across the interval it is paid for and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the attacker calls getReward immediately after a large stake by another user.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker calls getReward immediately after a large stake by another user, then assert `rewards[account]` and `IERC20(mgp).balanceOf(address(this))` end identical in both runs.
