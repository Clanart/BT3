# Q235: Accept wrong proof/network context in verify_tar_image_digest_inspect_only

## Question
Can an unprivileged attacker supply reorg timing around the same txid / outpoint / block height through broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation so `verify_tar_image_digest_inspect_only` accepts it without fully binding network, method-id, genesis, or height context, corrupting the canonical header-chain state and total work and breaking the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to High. Reorg-handling bug (Bitcoin-side or Citrea-side) that causes prolonged halt, inconsistent views, or unsafe rollback behavior?

## Target
- File/function: bridge-circuit-host/src/docker.rs::verify_tar_image_digest_inspect_only
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: reorg timing around the same txid / outpoint / block height
- Exploit idea: omit full network, method-id, genesis, or height binding for reorg timing around the same txid / outpoint / block height
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: High. Reorg-handling bug (Bitcoin-side or Citrea-side) that causes prolonged halt, inconsistent views, or unsafe rollback behavior
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
