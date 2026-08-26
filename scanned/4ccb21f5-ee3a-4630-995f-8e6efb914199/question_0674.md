# Q0674: MGPRelease.claim - the pre-start branch subtracts claimed from the initial tranche

## Question
rewards/MGPRelease.sol: getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. With the exact block at which the linear release is evaluated, and how often it is repeated under attacker control and the beneficiary claims repeatedly inside one block, can an unprivileged caller sequence `claim()` so that `sum of all totalAlloced` and `IERC20(tokenToRelease).balanceOf(address(this))` no longer reconcile, violating the invariant that a vesting accessor must never revert and must never underflow against a previously claimed amount and realising Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the pre-start branch subtracts claimed from the initial tranche)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Precondition: the beneficiary claims repeatedly inside one block.
- Invariant to test: a vesting accessor must never revert and must never underflow against a previously claimed amount; concretely, `sum of all totalAlloced` must stay reconciled with `IERC20(tokenToRelease).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under the beneficiary claims repeatedly inside one block, asserting at the end that `sum of all totalAlloced` still equals `IERC20(tokenToRelease).balanceOf(address(this))` and the PoC's balance delta is non-positive.
