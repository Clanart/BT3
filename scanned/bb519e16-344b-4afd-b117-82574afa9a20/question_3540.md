# Q3540: Drive state split inside verify_tar_image_digest

## Question
Can an unprivileged attacker use broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation with crafted Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents so `verify_tar_image_digest` updates one canonical value while another subsystem retains the older one for the same event, corrupting the canonical header-chain state and total work and breaking the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: bridge-circuit-host/src/docker.rs::verify_tar_image_digest
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: update one canonical value while another subsystem keeps the old one for the same event via Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
