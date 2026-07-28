# Q1713: ParseFractionalAmount RunAtomic rollback gap

## Question
Can an unprivileged attacker enter through call a stateful precompile through ordinary EVM transaction execution and use gas stipend, ABI-encoded args, caller contract structure, nested calls, and revert point so that `precompiles/common/utils.go:ParseFractionalAmount` mishandles stateful precompile helper path because `ParseFractionalAmount` may allow a stateful precompile path to commit part of its Cosmos-side work before `RunAtomic`/gas-error handling fully rolls back on error, causing `the Cosmos-side precompile write set` and `the final success/failure result of the enclosing call` to diverge or settle in the wrong order, breaking the invariant that stateful precompile helpers must guarantee all-or-nothing rollback under every error and out-of-gas path and leading to `Theft / unauthorized extraction of funds`?

## Target
- File/function: `precompiles/common/utils.go:ParseFractionalAmount`
- Entrypoint: call a stateful precompile through ordinary EVM transaction execution
- Attacker controls: gas stipend, ABI-encoded args, caller contract structure, nested calls, and revert point
- Exploit idea: Drive the stateful precompile helper path through a crafted path that reaches `ParseFractionalAmount` with attacker-controlled gas stipend, ABI-encoded args, caller contract structure, nested calls, and revert point. Then force the failure, replay, nested-call, or ordering condition described above and compare `the Cosmos-side precompile write set` against `the final success/failure result of the enclosing call`.
- Invariant to test: stateful precompile helpers must guarantee all-or-nothing rollback under every error and out-of-gas path
- Expected Immunefi impact: `Theft / unauthorized extraction of funds`
- Fast validation: replicate low-gas and nested-failure scenarios across several stateful precompiles and assert no side effect survives an error
