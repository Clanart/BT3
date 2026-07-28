# Q42: EmitWithdrawDelegatorRewardEvent reward partial write

## Question
Can an unprivileged attacker enter through call a public distribution precompile method from an EVM contract and use nested state writes plus deliberate outer-frame revert/out-of-gas timing; gas stipend, calldata, validator/delegator addresses, withdraw target, and outer-call revert timing so that `precompiles/distribution/events.go:EmitWithdrawDelegatorRewardEvent` mishandles withdrawal settlement because `EmitWithdrawDelegatorRewardEvent` can let a low-gas or nested-failure path transfer distribution value before the corresponding claim-tracking state is fully cleared, causing `the user-visible reward payout` and `the claimable reward accounting that should be reduced to match it` to diverge or settle in the wrong order, breaking the invariant that claiming or withdrawing rewards must atomically reduce claimable state whenever value leaves the distribution module and leading to `Theft / unauthorized extraction of funds`?

## Target
- File/function: `precompiles/distribution/events.go:EmitWithdrawDelegatorRewardEvent`
- Entrypoint: call a public distribution precompile method from an EVM contract
- Attacker controls: nested state writes plus deliberate outer-frame revert/out-of-gas timing; gas stipend, calldata, validator/delegator addresses, withdraw target, and outer-call revert timing
- Exploit idea: Drive the distribution precompile through a crafted path that reaches `EmitWithdrawDelegatorRewardEvent` with attacker-controlled nested state writes plus deliberate outer-frame revert/out-of-gas timing; gas stipend, calldata, validator/delegator addresses, withdraw target, and outer-call revert timing. Then force the failure, replay, nested-call, or ordering condition described above and compare `the user-visible reward payout` against `the claimable reward accounting that should be reduced to match it`.
- Invariant to test: claiming or withdrawing rewards must atomically reduce claimable state whenever value leaves the distribution module
- Expected Immunefi impact: `Theft / unauthorized extraction of funds`
- Fast validation: repeat the known partial-write style test with crafted gas boundaries and nested reverts and assert payout and claimable balances always change together
