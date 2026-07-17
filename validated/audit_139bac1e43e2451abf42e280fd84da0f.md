### Title
Missing Shard-ID Binding Check in `set_state_header` Allows Cross-Shard State Root Substitution — (File: chain/chain/src/state_sync/adapter.rs)

---

### Summary

`set_state_header` in `ChainStateSyncAdapter` validates that the chunk inside a peer-supplied `ShardStateSyncResponseHeader` is included in the block via a Merkle proof, but **never checks that `chunk.shard_id() == shard_id`**. Any unprivileged peer can supply a header for shard B when the syncing node requests a header for shard A. All existing checks pass, the header is stored under shard A's DB key with shard B's `prev_state_root`, and the node subsequently downloads and applies shard B's trie state for shard A.

---

### Finding Description

`set_state_header` performs five checks before persisting the header:

1. `validate_chunk_proofs` — verifies the chunk's internal hash/tx/receipt consistency.
2. `verify_path(sync_prev_block_header.chunk_headers_root(), chunk_proof, ChunkHashHeight(...))` — proves the chunk is *somewhere* in the block's chunk Merkle tree.
3. `verify_path` for the prev chunk — same, for the previous chunk.
4. Incoming-receipt proofs — uses the caller-supplied `shard_id` to hash receipts.
5. `validate_state_root