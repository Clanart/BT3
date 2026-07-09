# Q3757: NEAR Wormhole FinTransfer conversion shared proof response reused across entrypoints

## Question
Can an unprivileged attacker obtain a valid verifier result for one public flow and reuse it in `public Wormhole fee-claim proof flow` because `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<FinTransferMessage>` trusts the same response envelope under a different meaning, violating `fee-claim messages must remain bound to the exact destination event, amount, and fee recipient that the destination chain emitted`?

## Target
- File/function: `near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs::TryInto<FinTransferMessage>`
- Entrypoint: `public Wormhole fee-claim proof flow`
- Attacker controls: payload bytes inside a validated VAA, fee recipient string, transfer id, token address chain, and amount
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type.
- Invariant to test: fee-claim messages must remain bound to the exact destination event, amount, and fee recipient that the destination chain emitted
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics.
