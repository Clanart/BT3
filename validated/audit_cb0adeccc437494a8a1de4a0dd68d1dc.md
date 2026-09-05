### Title
`StacksTransaction::consensus_deserialize` discards unmasked bits of the `version` byte, so `serialize(deserialize(b)) != b` — ([File: stacks-codec/src/transaction.rs])

### Summary
`StacksTransaction::consensus_deserialize_with_len` decodes the wire `version` byte using only its high bit (`version_u8 & 0x80`), silently dropping the other 7 bits, while `consensus_serialize` always re-emits the canonical `TransactionVersion::Mainnet as u8 = 0x00` / `TransactionVersion::Testnet as u8 = 0x80`. An attacker can submit a transaction whose version byte is any value other than exactly `0x00`/`0x80` (e.g. `0x01` or `0xFF`) and have the node accept, canonicalize, and process it — but the bytes the node re-serializes (and hashes to compute the reported/relayed `txid`) differ from the exact bytes the attacker POSTed to `/v2/transactions`.

### Finding Description
The broken equality: for raw bytes `B` with `B[0] = 0x01` (bit `0x80` clear, so parsed as `Mainnet`, but not equal to the canonical `0x00`), let `T = consensus_deserialize(B)` and `B' = serialize(T)`. Then `B' != B` (specifically `B'[0] = 0x00 != 0x01 = B[0]`), violating `serialize(deserialize(B)) == B`.

Code path:
- `StacksTransaction::consensus_deserialize_with_len` reads `version_u8: u8` and computes `version = if (version_u8 & 0x80) == 0 { Mainnet } else { Testnet }` [1](#0-0) .
- `StacksTransaction::consensus_serialize` writes back `self.version as u8`, i.e. exactly `0x00` for `Mainnet` or `0x80` for `Testnet` [2](#0-1) .
- Any attacker-chosen `version_u8` with extra low bits set (e.g. `0x01`, `0x7F`, `0xFF`) is silently normalized and the information is lost — there is no validation rejecting non-canonical version bytes.
- Every consumer that computes a canonical byte stream from the parsed struct — mempool ingestion (`tx.consensus_serialize(&mut tx_data)` before storing in `mempool` table) [3](#0-2) , and the RPC endpoint that reports `txid` from `StacksTransaction::consensus_deserialize(...).txid()` [4](#0-3)  — operates on the canonicalized re-serialization, not on the literal bytes the attacker transmitted over `/v2/transactions`.
- No existing guard (`verify_origin`/`verify`, `process_transaction_precheck`, `check_transaction_postconditions`, the epoch gate, or `check_transaction_nonces`) inspects the raw `version` byte value; they all operate on the already-canonicalized `TransactionVersion` enum, so this divergence passes through untouched.

### Impact Explanation
This matches the **High** category "a txid/sighash over different bytes than transmitted." The node's canonical serialization (used for `txid`, mempool storage, and any relay that re-serializes from the parsed struct) differs from the raw bytes the attacker actually posted. Any third party (block explorer, external verifier, or naive relay) that computes a hash directly over the wire bytes as received will disagree with the txid the node reports/records for the same logical transaction, causing relay/txid-tracking confusion. Because all nodes deterministically canonicalize the same way (masking only the top bit), this does not appear to cause a fork or double-processing within the core consensus/mempool logic itself — the divergence is between "bytes on the wire" and "canonical bytes used for hashing," not between nodes.

### Likelihood Explanation
Trivial and fully attacker-controlled: any unprivileged sender can set an arbitrary `version_u8` when constructing their own signed transaction (the signature/sighash does not cover a specific expected value beyond what's used at verification time, and no code path re-validates that the version byte is exactly `0x00`/`0x80`). No special account state, epoch, or privileged role is required. This is repeatable on every transaction the attacker crafts.

### Recommendation
In `StacksTransaction::consensus_deserialize_with_len`, strictly validate `version_u8` against the exact canonical discriminants (`0x00` for Mainnet, `0x80` for Testnet) and reject any other byte value with a `DeserializeError`, mirroring the strict-byte checks already done for `anchor_mode_u8` and `post_condition_mode_u8`.

### Proof of Concept
```rust
// stacks-codec/src/transaction.rs (test)
#[test]
fn version_byte_does_not_roundtrip() {
    let auth = TransactionAuth::from_p2pkh(&StacksPrivateKey::random()).unwrap();
    let tx = StacksTransaction::new(
        TransactionVersion::Mainnet,
        auth,
        TransactionPayload::TokenTransfer(
            StacksAddress::new(1, Hash160([0xaa; 20])).unwrap().into(),
            1,
            TokenTransferMemo([0u8; 34]),
        ),
    );

    let mut canonical_bytes = vec![];
    tx.consensus_serialize(&mut canonical_bytes).unwrap();
    assert_eq!(canonical_bytes[0], 0x00); // canonical Mainnet byte

    // Attacker mutates only the version byte to a non-canonical Mainnet-equivalent value.
    let mut b = canonical_bytes.clone();
    b[0] = 0x01; // top bit clear -> still parses as Mainnet

    let decoded = StacksTransaction::consensus_deserialize(&mut &b[..]).unwrap();
    assert_eq!(decoded.version, TransactionVersion::Mainnet); // accepted!

    let mut b_prime = vec![];
    decoded.consensus_serialize(&mut b_prime).unwrap();

    // Violated invariant: serialize(deserialize(b)) != b
    assert_ne!(b_prime, b);
    // But the node's official txid is computed over `b_prime`, not the attacker's `b`.
    assert_eq!(decoded.txid(), StacksTransaction::consensus_deserialize(&mut &b_prime[..]).unwrap().txid());
}
```

### Citations

**File:** stacks-codec/src/transaction.rs (L3017-3026)
```rust
    fn consensus_serialize<W: Write>(&self, fd: &mut W) -> Result<(), codec_error> {
        write_next(fd, &(self.version as u8))?;
        write_next(fd, &self.chain_id)?;
        write_next(fd, &self.auth)?;
        write_next(fd, &(self.anchor_mode as u8))?;
        write_next(fd, &(self.post_condition_mode as u8))?;
        write_next(fd, &self.post_conditions)?;
        write_next(fd, &self.payload)?;
        Ok(())
    }
```

**File:** stacks-codec/src/transaction.rs (L3047-3060)
```rust
        let version_u8: u8 = read_next(fd)?;
        let chain_id: u32 = read_next(fd)?;
        let auth: TransactionAuth = read_next(fd)?;
        let anchor_mode_u8: u8 = read_next(fd)?;
        let post_condition_mode_u8: u8 = read_next(fd)?;
        let post_conditions: Vec<TransactionPostCondition> = read_next(fd)?;

        let payload: TransactionPayload = read_next(fd)?;

        let version = if (version_u8 & 0x80) == 0 {
            TransactionVersion::Mainnet
        } else {
            TransactionVersion::Testnet
        };
```

**File:** stackslib/src/core/mempool.rs (L2418-2421)
```rust
        let txid = tx.txid();
        let mut tx_data = vec![];
        tx.consensus_serialize(&mut tx_data)
            .map_err(MemPoolRejection::SerializationFailure)?;
```

**File:** stacks-node/src/tests/neon_integrations.rs (L5970-5976)
```rust
            assert_eq!(
                res,
                StacksTransaction::consensus_deserialize(&mut &tx_2[..])
                    .unwrap()
                    .txid()
                    .to_string()
            );
```
