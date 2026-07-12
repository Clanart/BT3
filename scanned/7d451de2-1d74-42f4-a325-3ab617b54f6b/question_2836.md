# Q2836: SafeNewIntFromBigInt - Negative Big Int Enters Sdk Int Fee Math

## Question
Can an unprivileged attacker submit boundary-value transaction fields through public tx or RPC paths through `big integer conversion for tx values and fees` while controlling `sdk.Int conversion` and `fee+value sum`, under the precondition that conversion happens before validation in one path, drive `tx validation -> cost calculation -> bank keeper debit/credit` in `types/int.go::SafeNewIntFromBigInt` so that negative big.Int enters sdk.Int fee math, violating the invariant that integer conversion must reject values outside the supported range, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `types/int.go::SafeNewIntFromBigInt`
- Entrypoint: `big integer conversion for tx values and fees`
- Attacker controls: `sdk.Int conversion`, `fee+value sum`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: negative big.Int enters sdk.Int fee math through `tx validation -> cost calculation -> bank keeper debit/credit`.
- Invariant to test: integer conversion must reject values outside the supported range.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: construct a contract harness that performs nested CALL/CREATE/SELFDESTRUCT/revert and compare bank keeper balances with StateDB balances.
