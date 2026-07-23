# Q1179: Exploit ordering assumptions in verify_tar_image_digest_inspect_only

## Question
Can an unprivileged attacker use attacker-controlled Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents so `verify_tar_image_digest_inspect_only` validates the right inputs in the wrong order, corrupting the L1 block hash carried from the light-client proof into bridge validation and breaking the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: bridge-circuit-host/src/docker.rs::verify_tar_image_digest_inspect_only
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: validate the right pieces in the wrong order using Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
