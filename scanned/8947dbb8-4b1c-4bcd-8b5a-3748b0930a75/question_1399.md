# Q1399: Execute partial bank send

## Question
Can an unprivileged attacker enter through call a public bank precompile method from an EVM contract and use attacker-controlled contract bytecode, call graph, and revert point; recipient, amount, denom, gas stipend, calldata, and nested-call / revert timing so that `precompiles/bank/bank.go:Execute` mishandles bank precompile because `Execute` can let a bank-side send or module-account update survive even though the enclosing EVM or precompile path later errors, causing `the bank-side token movement` and `the enclosing precompile/EVM success state` to diverge or settle in the wrong order, breaking the invariant that bank-side value movement through precompiles must be atomic with the enclosing transaction outcome and leading to `Theft / unauthorized extraction of funds`?

## Target
- File/function: `precompiles/bank/bank.go:Execute`
- Entrypoint: call a public bank precompile method from an EVM contract
- Attacker controls: attacker-controlled contract bytecode, call graph, and revert point; recipient, amount, denom, gas stipend, calldata, and nested-call / revert timing
- Exploit idea: Drive the bank precompile through a crafted path that reaches `Execute` with attacker-controlled attacker-controlled contract bytecode, call graph, and revert point; recipient, amount, denom, gas stipend, calldata, and nested-call / revert timing. Then force the failure, replay, nested-call, or ordering condition described above and compare `the bank-side token movement` against `the enclosing precompile/EVM success state`.
- Invariant to test: bank-side value movement through precompiles must be atomic with the enclosing transaction outcome
- Expected Immunefi impact: `Theft / unauthorized extraction of funds`
- Fast validation: call the bank precompile from a contract, revert after the inner send, and assert no account or module balance changed
