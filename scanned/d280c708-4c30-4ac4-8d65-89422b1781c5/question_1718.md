# Q1718: FundCommunityPool outer-revert leak

## Question
Can an unprivileged attacker enter through call a public distribution precompile method from an EVM contract and use ABI-encoded calldata arguments; attacker-controlled contract bytecode, call graph, and revert point; nested state writes plus deliberate outer-frame revert/out-of-gas timing; gas stipend, calldata, validator/delegator addresses, withdraw target, and outer-call revert timing so that `precompiles/distribution/tx.go:FundCommunityPool` mishandles distribution precompile because `FundCommunityPool` can complete a distribution-side mutation inside a nested call and then let the outer EVM frame revert, potentially keeping value movement without the expected top-level success, causing `the distribution-side state write` and `the top-level transaction success state` to diverge or settle in the wrong order, breaking the invariant that no distribution-side mutation may survive if the enclosing top-level EVM transaction reverts and leading to `Theft / unauthorized extraction of funds`?

## Target
- File/function: `precompiles/distribution/tx.go:FundCommunityPool`
- Entrypoint: call a public distribution precompile method from an EVM contract
- Attacker controls: ABI-encoded calldata arguments; attacker-controlled contract bytecode, call graph, and revert point; nested state writes plus deliberate outer-frame revert/out-of-gas timing; gas stipend, calldata, validator/delegator addresses, withdraw target, and outer-call revert timing
- Exploit idea: Drive the distribution precompile through a crafted path that reaches `FundCommunityPool` with attacker-controlled ABI-encoded calldata arguments; attacker-controlled contract bytecode, call graph, and revert point; nested state writes plus deliberate outer-frame revert/out-of-gas timing; gas stipend, calldata, validator/delegator addresses, withdraw target, and outer-call revert timing. Then force the failure, replay, nested-call, or ordering condition described above and compare `the distribution-side state write` against `the top-level transaction success state`.
- Invariant to test: no distribution-side mutation may survive if the enclosing top-level EVM transaction reverts
- Expected Immunefi impact: `Theft / unauthorized extraction of funds`
- Fast validation: call the target from a helper contract that reverts after the inner precompile call and assert no payout or accounting change survives
