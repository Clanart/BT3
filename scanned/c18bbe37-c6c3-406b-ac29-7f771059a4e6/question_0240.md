# Q0240: MGPRelease.claim - the pre-start branch subtracts claimed from the initial tranche

## Question
rewards/MGPRelease.sol: getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Under block.timestamp is exactly startTimestamp, is there an unprivileged sequence of `claim()` that leaves `initialUnlockedAmount` unreconciled with `beneficiaries[account].claimed`, violates the invariant that a vesting accessor must never revert and must never underflow against a previously claimed amount, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the pre-start branch subtracts claimed from the initial tranche)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Precondition: block.timestamp is exactly startTimestamp.
- Invariant to test: a vesting accessor must never revert and must never underflow against a previously claimed amount; concretely, `initialUnlockedAmount` must stay reconciled with `beneficiaries[account].claimed`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that block.timestamp is exactly startTimestamp, fuzz the attacker inputs (the exact block at which the linear release is evaluated, and how often it is repeated), and assert after every call that a vesting accessor must never revert and must never underflow against a previously claimed amount.
