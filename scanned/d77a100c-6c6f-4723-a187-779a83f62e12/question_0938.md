# Q938: SafeNewIntFromBigInt - Oversized Uint256 Value Saturates Instead Of Rejecting

## Question
Can an unprivileged attacker submit boundary-value transaction fields through public tx or RPC paths through `big integer conversion for tx values and fees` while controlling `nil amount` and `uint256 boundary`, under the precondition that the sender balance is near the fee plus value boundary, drive `public tx/RPC input -> big.Int/sdk.Int conversion -> fee/value/balance comparison` in `types/int.go::SafeNewIntFromBigInt` so that oversized uint256 value saturates instead of rejecting, violating the invariant that bank supply must equal the sum of account balances plus module balances, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `types/int.go::SafeNewIntFromBigInt`
- Entrypoint: `big integer conversion for tx values and fees`
- Attacker controls: `nil amount`, `uint256 boundary`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: oversized uint256 value saturates instead of rejecting through `public tx/RPC input -> big.Int/sdk.Int conversion -> fee/value/balance comparison`.
- Invariant to test: bank supply must equal the sum of account balances plus module balances.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: build a two-message Cosmos tx fixture and assert ante, execution, refund, and receipt invariants after FinalizeBlock.
