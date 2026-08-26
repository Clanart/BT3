# Q0644: WomUp.withdraw - stake and withdraw inside one block capture an interval

## Question
In wombat/WomUp.sol, stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Starting from a state where the attacker funds the stake with a flash loan of WOM repaid in the same transaction, can an unprivileged EOA use `withdraw(uint256 amount, bool claim)` to leave `rewards[account]` inconsistent with `IERC20(mgp).balanceOf(address(this))`, violating the invariant that reward share must require the stake to have been held across the interval it is paid for and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the attacker funds the stake with a flash loan of WOM repaid in the same transaction.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker funds the stake with a flash loan of WOM repaid in the same transaction, snapshot `rewards[account]` and `IERC20(mgp).balanceOf(address(this))`, run the attacker's `withdraw(uint256 amount, bool claim)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
