[1](#0-0) [2](#0-1)

### Citations

**File:** entry/src/entry.rs (L128-135)
```rust
#[derive(Clone, Debug, PartialEq, Eq)]
struct TxVerificationData {
    is_simple_vote: bool,
    signatures: SmallVec<[Signature; 2]>,
    signer_pubkeys: SmallVec<[Address; 2]>,
    message_hash: Hash,
    serialized_message: Vec<u8>,
}
```

**File:** entry/src/entry.rs (L193-204)
```rust
    pub fn vote_transaction_message_hashes(&self) -> Vec<Hash> {
        self.signatures
            .iter()
            .filter(|tx_signatures| tx_signatures.is_simple_vote)
            .filter_map(|tx_signatures| {
                tx_signatures
                    .signatures
                    .first()
                    .map(|_| tx_signatures.message_hash)
            })
            .collect()
    }
```
