### Title
`_compute_state_diff_length` Omits `deprecated_declared_classes`, Producing Wrong `concatenated_counts` and Wrong Block Hash Reconstruction — (File: `echonet/echo_center.py`)

---

### Summary

`BlobTransformer._compute_state_diff_length` in `echonet/echo_center.py` computes `state_diff_length` without counting `deprecated_declared_classes`. The canonical Rust implementation `ThinStateDiff::len()` does include them. The stored `state_diff_length` is later used to reconstruct `concatenated_counts` — a packed field that is a direct input to `calculate_block_hash`. Any block that declares at least one deprecated (Cairo 0) class will have a stored `state_diff_length` that is too small by exactly the number of deprecated classes declared, causing every downstream consumer that reconstructs `BlockHeaderCommitments` from the stored header to derive a wrong `concatenated_counts` and therefore a wrong block hash.

---

### Finding Description

**Canonical length computation — Rust**

`ThinStateDiff::len()` in `crates/starknet_api/src/state.rs` counts five categories:

```
deployed_contracts  +  class_hash_to_compiled_class_hash
  +  deprecated_declared_classes          ← included
  +  nonces  +  Σ storage_slots
``` [1](#0-0) 

This value is passed directly to `concat_counts` inside `calculate_block_commitments`, which packs it into the `concatenated_counts` felt that enters the Poseidon block-hash chain: [2](#0-1) [3](#0-2) 

`concatenated_counts` is chained at position 6 of the block hash: [4](#0-3) 

**Defective length computation — Python**

`_compute_state_diff_length` in `echonet/echo_center.py` counts only four categories:

```python
return (
    len(state_diff["address_to_class_hash"])
    + len(state_diff["class_hash_to_compiled_class_hash"])
    + len(state_diff["nonces"]["L1"])
    + sum(len(slots) for slots in storage_updates.values())
)
# deprecated_declared_classes is never added
``` [5](#0-4) 

The result is written into the block document as `block_document["state_diff_length"]`: [6](#0-5) 

**Reconstruction path that consumes the wrong value**

`TryFrom<&BlockHeader> for Option<BlockHeaderCommitments>` reads the stored `state_diff_length` and calls `concat_counts` with it to rebuild `concatenated_counts`: [7](#0-6) 

Any consumer that reconstructs `BlockHeaderCommitments` from the stored header — including the P2P sync server, which already has a fallback path that recomputes `state_diff_length` from the stored `ThinStateDiff` precisely because it knows the field can be wrong — will derive a `concatenated_counts` that differs from the one used when the block hash was originally computed: [8](#0-7) 

---

### Impact Explanation

For every block that contains at least one deprecated (Cairo 0) class declaration, the stored `state_diff_length` is `N` less than the true value, where `N` is the count of deprecated classes declared in that block. This causes:

1. **Wrong `concatenated_counts`** when `BlockHeaderCommitments` is reconstructed from the stored header. Because `concatenated_counts` is a direct input to `calculate_block_hash`, any re-derivation of the block hash from the stored header produces a hash that does not match the authoritative hash stored in the block document.
2. **Wrong `state_diff_length` returned by the RPC** — an authoritative-looking wrong value served to all clients querying block headers.

This matches the allowed impact: *High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value*, and potentially *Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result* if the wrong `concatenated_counts` is used to accept or reject a block commitment.

---

### Likelihood Explanation

Deprecated (Cairo 0) class declarations occur on Starknet mainnet via old `Deploy` transactions and legacy `Declare` transactions. Any historical block containing such a transaction triggers the discrepancy. The bug is silent — no error is raised — and the wrong value is stored persistently. The trigger requires no special privilege; it is a property of the block content.

---

### Recommendation

Replace the Python `_compute_state_diff_length` with a formula that mirrors `ThinStateDiff::len()` exactly, adding the count of deprecated declared classes:

```python
@staticmethod
def _compute_state_diff_length(blob: JsonObject) -> int:
    state_diff = blob["state_diff"]
    storage_updates = state_diff["storage_updates"]["L1"]
    return (
        len(state_diff["address_to_class_hash"])
        + len(state_diff["class_hash_to_compiled_class_hash"])
        + len(state_diff["nonces"]["L1"])
        + sum(len(slots) for slots in storage_updates.values())
        + len(state_diff.get("deprecated_declared_classes", []))  # ← add this
    )
```

Alternatively, delegate the computation entirely to the Rust CLI (the same binary already invoked for block-hash commitments) so that the Python and Rust implementations cannot diverge again.

---

### Proof of Concept

**Setup:** A block that declares one Cairo 0 class (one entry in `deprecated_declared_classes`).

**Rust canonical value** (`ThinStateDiff::len()`):
```
deployed=0, class_hash_to_compiled=0, deprecated=1, nonces=0, storage=0
→ len() = 1
``` [1](#0-0) 

**Python stored value** (`_compute_state_diff_length`):
```
address_to_class_hash=0, class_hash_to_compiled=0, nonces=0, storage=0
→ result = 0   (deprecated_declared_classes never counted)
``` [5](#0-4) 

**`concat_counts` with correct value (1):**
```
concatenated_counts = 0x0000000000000000 0000000000000000 0000000000000001 <DA_bit> ...
```

**`concat_counts` with stored wrong value (0):**
```
concatenated_counts = 0x0000000000000000 0000000000000000 0000000000000000 <DA_bit> ...
``` [3](#0-2) 

The two `concatenated_counts` felts differ. When `calculate_block_hash` is called with the reconstructed `BlockHeaderCommitments` (using the stored wrong `state_diff_length = 0`), it produces a block hash that does not match the authoritative hash stored in the block document, which was computed with `state_diff_length = 1`. [4](#0-3)

### Citations

**File:** crates/starknet_api/src/state.rs (L111-122)
```rust
    pub fn len(&self) -> usize {
        let mut result = 0usize;
        result += self.deployed_contracts.len();
        result += self.class_hash_to_compiled_class_hash.len();
        result += self.deprecated_declared_classes.len();
        result += self.nonces.len();

        for (_contract_address, storage_diffs) in &self.storage_diffs {
            result += storage_diffs.len();
        }
        result
    }
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L139-182)
```rust
impl TryFrom<&BlockHeader> for Option<BlockHeaderCommitments> {
    type Error = StarknetApiError;
    fn try_from(block_header: &BlockHeader) -> Result<Self, Self::Error> {
        match (
            block_header.state_diff_commitment,
            block_header.transaction_commitment,
            block_header.event_commitment,
            block_header.receipt_commitment,
            block_header.state_diff_length,
        ) {
            (
                Some(state_diff_commitment),
                Some(transaction_commitment),
                Some(event_commitment),
                Some(receipt_commitment),
                Some(state_diff_length),
            ) => Ok(Some(BlockHeaderCommitments {
                transaction_commitment,
                event_commitment,
                receipt_commitment,
                state_diff_commitment,
                concatenated_counts: concat_counts(
                    block_header.n_transactions,
                    block_header.n_events,
                    state_diff_length,
                    block_header.block_header_without_hash.l1_da_mode,
                ),
            })),
            _ => {
                if block_header
                    .block_header_without_hash
                    .starknet_version
                    .has_partial_block_hash_components()
                {
                    Err(StarknetApiError::MissingBlockHeaderCommitments {
                        block_number: block_header.block_header_without_hash.block_number,
                        version: block_header.block_header_without_hash.starknet_version,
                    })
                } else {
                    Ok(None)
                }
            }
        }
    }
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-281)
```rust
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
            .chain(
                &Felt::try_from(&partial_block_hash_components.starknet_version)
                    .expect("Expect ASCII version"),
            )
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
    ))
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-323)
```rust
    let concatenated_counts = concat_counts(
        transactions_data.len(),
        event_leaf_elements.len(),
        state_diff.len(),
        l1_da_mode,
    );
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L374-393)
```rust
pub fn concat_counts(
    transaction_count: usize,
    event_count: usize,
    state_diff_length: usize,
    l1_data_availability_mode: L1DataAvailabilityMode,
) -> Felt {
    let l1_data_availability_byte: u8 = match l1_data_availability_mode {
        L1DataAvailabilityMode::Calldata => 0,
        L1DataAvailabilityMode::Blob => 0b10000000,
    };
    let concat_bytes = [
        to_64_bits(transaction_count).as_slice(),
        to_64_bits(event_count).as_slice(),
        to_64_bits(state_diff_length).as_slice(),
        &[l1_data_availability_byte],
        &[0_u8; 7], // zero padding
    ]
    .concat();
    Felt::from_bytes_be_slice(concat_bytes.as_slice())
}
```

**File:** echonet/echo_center.py (L552-561)
```python
    @staticmethod
    def _compute_state_diff_length(blob: JsonObject) -> int:
        state_diff = blob["state_diff"]
        storage_updates = state_diff["storage_updates"]["L1"]
        return (
            len(state_diff["address_to_class_hash"])
            + len(state_diff["class_hash_to_compiled_class_hash"])
            + len(state_diff["nonces"]["L1"])
            + sum(len(slots) for slots in storage_updates.values())
        )
```

**File:** echonet/echo_center.py (L760-760)
```python
        block_document["state_diff_length"] = self._compute_state_diff_length(blob)
```

**File:** crates/apollo_p2p_sync/src/server/mod.rs (L229-238)
```rust
        // TODO(shahak): Remove this once central sync fills the state_diff_length field.
        if header.state_diff_length.is_none() {
            header.state_diff_length = Some(
                txn.get_state_diff(block_number)?
                    .ok_or(P2pSyncServerError::BlockNotFound {
                        block_hash_or_number: BlockHashOrNumber::Number(block_number),
                    })?
                    .len(),
            );
        }
```
