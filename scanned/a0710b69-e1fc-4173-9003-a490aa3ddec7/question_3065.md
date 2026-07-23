# Q3065: Corrupt work or canonical ordering in verify_proof

## Question
Can an unprivileged attacker shape Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents so `verify_proof` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the canonical header-chain state and total work and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/header_chain/mmr_native.rs::verify_proof
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: make the wrong chain or watchtower result win by shaping Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
