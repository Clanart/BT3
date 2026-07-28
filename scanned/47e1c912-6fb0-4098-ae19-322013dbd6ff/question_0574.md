# Q574: Run bank sender confusion

## Question
Can an unprivileged attacker enter through call a public bank precompile method from an EVM contract and use attacker-controlled contract bytecode, call graph, and revert point; recipient, amount, denom, gas stipend, calldata, and nested-call / revert timing so that `precompiles/bank/bank.go:Run` mishandles precompile dispatch because `Run` may bind the wrong caller/sender identity when called through contracts or proxies, enabling unauthorized bank-side transfers or approvals, causing `the account authorized to spend` and `the account whose balance is actually debited` to diverge or settle in the wrong order, breaking the invariant that no bank-side value movement may debit an account that did not authorize the exact action and leading to `Privilege escalation / authorization bypass / unauthorized state mutation`?

## Target
- File/function: `precompiles/bank/bank.go:Run`
- Entrypoint: call a public bank precompile method from an EVM contract
- Attacker controls: attacker-controlled contract bytecode, call graph, and revert point; recipient, amount, denom, gas stipend, calldata, and nested-call / revert timing
- Exploit idea: Drive the bank precompile through a crafted path that reaches `Run` with attacker-controlled attacker-controlled contract bytecode, call graph, and revert point; recipient, amount, denom, gas stipend, calldata, and nested-call / revert timing. Then force the failure, replay, nested-call, or ordering condition described above and compare `the account authorized to spend` against `the account whose balance is actually debited`.
- Invariant to test: no bank-side value movement may debit an account that did not authorize the exact action
- Expected Immunefi impact: `Privilege escalation / authorization bypass / unauthorized state mutation`
- Fast validation: attempt proxy and nested-call spends in tests and assert only the exact authorized account can be debited
