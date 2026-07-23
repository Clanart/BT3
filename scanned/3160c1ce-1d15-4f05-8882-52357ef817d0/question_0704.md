# Q704: Exploit reorg boundary handling in verify_proof

## Question
Can an unprivileged attacker exploit reorg timing around Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents so `verify_proof` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the SPV inclusion result for the payout transaction and violating the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency?

## Target
- File/function: circuits-lib/src/header_chain/mmr_guest.rs::verify_proof
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: reorder or replay Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents across canonical and non-canonical views
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
