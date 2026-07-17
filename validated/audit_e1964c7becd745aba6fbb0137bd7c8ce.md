### Title
Unauthenticated `SpicePartialDataRequest` Triggers Unbounded Reed-Solomon Encoding on Chunk Producers — (`File: chain/client/src/spice/data_distributor_actor.rs`)

### Summary

`handle_partial_data_request` in `SpiceDataDistributorActor` serves witness and receipt-proof data to any peer that sends a `SpicePartialDataRequest`, without verifying that the `requester` field belongs to a legitimate chunk-validator recipient. A malicious peer can flood a chunk producer with requests for valid `(block_hash, shard_id)` pairs, forcing repeated full Reed-Solomon encoding of witness data at no cost to the attacker. The missing check is explicitly acknowledged in a TODO comment in the production code.

### Finding Description

`handle_partial_data_request` (lines 1001–1048 of `chain/client/src/spice/data_distributor_actor.rs`) processes incoming `SpicePartialDataRequest` messages. The function verifies only that the **local node** is a producer of the requested data:

```rust
if !producers.contains(signer.validator_id()) {
    return Err(Error::Other("we do not produce requested data"));
}
```

It then immediately calls `get_distribution_data`, which calls `encode_distribution_data`, performing a full Reed-Solomon encoding of the witness or receipt proof. The encoded data is sent to whoever the `requester` field claims to be, with no validation that the requester is a legitimate chunk-validator recipient. The code itself documents this gap:

```rust
// TODO(spice): Check that requester is one of the recipients and implement a
// lower-priority way for other nodes that aren't validators (e.g. rpc nodes) to get
// data they require.
```

The `SpicePartialDataRequest` struct carries only an unauthenticated `AccountId` as the requester:

```rust
pub struct SpicePartialDataRequest {
    pub data_id: SpiceDataIdentifier,
    pub requester: AccountId,
}
```

The integration test `test_requesting_receipts_when_not_validator` (lines 2106–2155) confirms that a node with `AccountId::from_str("not-validator")` successfully receives full encoded data from a producer.

The `recipients_and_producers` function (lines 455–496) computes the legitimate recipient set from epoch-manager state, but this set is never compared against the `requester` field before encoding and sending.

### Impact Explanation

Each `SpicePartialDataRequest` for a valid `(block_hash, shard_id)` pair causes the chunk producer to:

1. Fetch the block from the chain store.
2. Perform epoch-manager lookups to compute producers and recipients.
3. **Reed-Solomon encode the entire witness** (or receipt proof) via `encode_distribution_data`, which includes Merkle-tree construction over all parts.
4. For witness requests: additionally fetch and sign contract accesses, then send them.
5. Sign and transmit the full encoded payload to the claimed requester.

A malicious peer can continuously send requests for different valid block hashes across all shards, causing the chunk producer to spend its CPU budget on RS encoding instead of producing chunks. This is a direct analog to the in3-server bug: one cheap request triggers proportional expensive computation on the server, with no cost to the requester.

**Impact: High** — chunk producers are a small, known set of nodes; sustained flooding can delay or prevent chunk production, stalling block finality.

### Likelihood Explanation

`SpicePartialDataRequest` is a T1 (high-priority) routed message. Any peer that has established a network connection can send it. The `RateLimitedPeerMessageKey::SpicePartialDataRequest` key exists in the rate-limiting infrastructure, but `Config::standard_preset()` (lines 105–122 of `chain/network/src/rate_limits/messages_limits.rs`) configures no default rate limit for it — only `EpochSyncRequest` and `EpochSyncResponse` have preset limits. The attacker needs only a valid `block_hash` (publicly available from the chain) and a valid `shard_id`.

### Recommendation

Inside `handle_partial_data_request`, after computing `recipients_and_producers`, add a check that `requester` is a member of the computed `recipients` set before calling `get_distribution_data`. The TODO comment at line 1023 already identifies this as the correct fix. A secondary defense is to activate a default rate limit for `SpicePartialDataRequest` in `Config::standard_preset()`.

### Proof of Concept

1. Connect to a chunk-producer node as any peer.
2. Enumerate recent block hashes from the public chain (e.g., via `block` RPC).
3. For each block hash and each shard ID, send a `SpicePartialDataRequest` with `data_id = SpiceDataIdentifier::Witness { block_hash, shard_id }` and `requester = <any AccountId>`.
4. The producer calls `encode_distribution_data` for each request, performing full RS encoding of the witness.
5. Repeat at the maximum network rate; no rate limit is enforced by default.

The existing test `test_requesting_receipts_when_not_validator` already demonstrates step 3–4 with a non-validator requester receiving a complete encoded response.

---

**Key code references:**

Missing requester validation with explicit TODO acknowledgement: [1](#0-0) 

Expensive RS encoding triggered unconditionally: [2](#0-1) 

`encode_distribution_data` performing full RS encoding and Merkle construction: [3](#0-2) 

Unauthenticated `requester` field in the request struct: [4](#0-3) 

No default rate limit configured for `SpicePartialDataRequest`: [5](#0-4) 

Test confirming non-validators receive full data: [6](#0-5)

### Citations

**File:** chain/client/src/spice/data_distributor_actor.rs (L417-449)
```rust
    fn encode_distribution_data(
        &mut self,
        data: &SpiceData,
        total_parts: usize,
    ) -> DistributionData {
        let encoder = self.rs_encoders.entry(total_parts);
        let (boxed_parts, encoded_length) = encoder.encode(data);
        debug_assert_eq!(boxed_parts.len(), total_parts);

        let parts: Vec<&[u8]> =
            boxed_parts.iter().map(|x| x.as_deref().unwrap()).collect::<Vec<_>>();
        let (merkle_root, merkle_proofs) = merklize(&parts);
        // TODO(spice): As an optimization we should be able to avoid serializing data both in
        // encode and to compute hash.
        let data_hash = hash(&borsh::to_vec(&data).unwrap());
        let commitment = SpiceDataCommitment {
            hash: data_hash,
            root: merkle_root,
            encoded_length: encoded_length as u64,
        };

        debug_assert_eq!(boxed_parts.len(), merkle_proofs.len());
        let parts = boxed_parts
            .into_iter()
            .zip(merkle_proofs)
            .enumerate()
            .map(|(part_ord, (boxed_part, merkle_proof))| SpiceDataPart {
                part_ord: part_ord as u64,
                part: boxed_part.unwrap(),
                merkle_proof,
            })
            .collect_vec();
        DistributionData { commitment, parts }
```

**File:** chain/client/src/spice/data_distributor_actor.rs (L1012-1025)
```rust
        let (_recipients, producers) = self.recipients_and_producers(&data_id, &block)?;
        if !producers.contains(signer.validator_id()) {
            return Err(Error::Other("we do not produce requested data"));
        }

        let Some(data) = self.get_distribution_data(&data_id, producers.len()) else {
            // TODO(spice): Make sure we send requests for data only after we know it may be
            // available and make this into error.
            tracing::debug!(target:"spice_data_distribution", ?data_id, ?requester, "received request for unknown data");
            return Ok(());
        };
        // TODO(spice): Check that requester is one of the recipients and implement a
        // lower-priority way for other nodes that aren't validators (e.g. rpc nodes) to get
        // data they require.
```

**File:** chain/network/src/spice/data_distribution.rs (L14-18)
```rust
#[derive(Debug, Clone, PartialEq, Eq, borsh::BorshSerialize, borsh::BorshDeserialize)]
pub struct SpicePartialDataRequest {
    pub data_id: SpiceDataIdentifier,
    pub requester: AccountId,
}
```

**File:** chain/network/src/rate_limits/messages_limits.rs (L105-122)
```rust
    pub fn standard_preset() -> Self {
        // TODO(trisfald): make presets for other message types
        let mut config = Self::default();
        // EpochSyncRequest is a very simple amplification attack vector, as it requires no arguments
        // and the response is large. So we rate limit it to 1 request per 30 seconds. In practice,
        // a peer should not need to epoch sync except when bootstrapping a node, so a request
        // should be rarely received. We still set it to a reasonable rate limit so a bootstrapping
        // node can retry without waiting for too long.
        config.rate_limits.insert(
            RateLimitedPeerMessageKey::EpochSyncRequest,
            SingleMessageConfig::new(1, 1.0 / 30.0, None),
        );
        config.rate_limits.insert(
            RateLimitedPeerMessageKey::EpochSyncResponse,
            SingleMessageConfig::new(1, 1.0 / 30.0, None),
        );
        config
    }
```

**File:** chain/client/src/spice/tests/data_distributor_actor.rs (L2106-2132)
```rust
#[test]
#[cfg_attr(not(feature = "protocol_feature_spice"), ignore)]
fn test_requesting_receipts_when_not_validator() {
    let (genesis, chain) = setup(2, 1);
    let requester_chain = new_chain(&chain, &genesis);

    let block = latest_block(&chain);
    let receipt_proof = new_test_receipt_proof(&block);
    let mut store_update = chain.chain_store.store().store_update();
    save_receipt_proof(&mut store_update, block.hash(), &receipt_proof);
    store_update.commit();

    let producer = producers_of_receipt_proof(&chain, &block, &receipt_proof).swap_remove(0);
    let data_id = SpiceDataIdentifier::ReceiptProof {
        block_hash: *block.hash(),
        from_shard_id: receipt_proof.1.from_shard_id,
        to_shard_id: receipt_proof.1.to_shard_id,
    };
    let to_shard_id = receipt_proof.1.to_shard_id;

    let (outgoing_sc, mut outgoing_rc) = unbounded_channel();
    let mut actor = new_actor_for_account(outgoing_sc, &chain, &producer);

    let requester = AccountId::from_str("not-validator").unwrap();
    actor.handle(SpicePartialDataRequest { data_id, requester: requester.clone() });
    let (partial_data, recipients) = drain_outgoing_partial_data(&mut outgoing_rc).swap_remove(0);
    assert_eq!(recipients, HashSet::from([requester.clone()]));
```
