Now I have enough information to make a definitive determination. Let me check one more thing about whether the header's block hash is verified against L1 in the P2P sync path.

Based on my thorough analysis of the code, here is the determination:

---

### Title
Unvalidated `transaction_output` from P2P peer stored without commitment verification — (`crates/apollo_p2p_sync/src/client/transaction.rs`)

### Summary
`TransactionStreamFactory::parse_data_for_block` accepts peer-supplied `transaction_output` fields (events, messages_to_l1, actual_fee, execution_status, gas_consumed) and stores them verbatim with no verification against the header's `event_commitment` or `receipt_commitment`. Any P2P peer can corrupt the stored `TransactionOutput` for any block, causing RPC endpoints to serve authoritative-looking wrong values.

### Finding Description

In `parse_data_for_block`, the only guard on a received `FullTransaction` is a count check against `header.n_transactions`: [1](#0-0) 

After that, `transaction_output` is pushed directly into `block_body` with no further validation: [2](#0-1) 

The developer-acknowledged TODO on line 88 (`// TODO(eitan): Validate transaction hash from untrusted sources`) confirms that peer data is known to be untrusted and unvalidated. The same absence of validation applies to the entire `transaction_output` struct.

`write_to_storage` then calls `append_body` unconditionally: [3](#0-2) 

`append_body` in storage writes the outputs to disk with no commitment cross-check: [4](#0-3) 

The header itself is also accepted from the P2P network with only a block-number ordering check and a signature-length check — no block hash is verified against L1 or any trusted anchor in the P2P sync path: [5](#0-4) 

This means a malicious peer can supply a forged header (with arbitrary `event_commitment`/`receipt_commitment`) together with forged `transaction_output` data that is internally consistent with those forged commitments, and the node will store both without complaint.

The commitment functions that *should* be used for verification exist and are well-defined: [6](#0-5) [7](#0-6) 

but they are never called in the P2P sync client path.

### Impact Explanation

Once stored, the forged `TransactionOutput` is served directly by RPC endpoints. For example, `starknet_getTransactionReceipt` and `starknet_getBlockWithReceipts` read `transaction_outputs` from storage and return them as authoritative results: [8](#0-7) 

A malicious peer can forge: empty event lists, zero fees, false execution success/revert status, wrong L1 messages, and wrong gas figures — all of which will be stored and served to downstream clients as if they were the canonical execution result.

### Likelihood Explanation

Any node participating in the P2P network can act as the transaction-stream peer. No special privileges are required. The header stream and transaction stream are separate (`header_stream.merge(...).merge(transaction_stream)...`), so a malicious peer need only control the transaction sub-protocol to inject forged outputs against legitimately-sourced headers. [9](#0-8) 

### Recommendation

After collecting all `transaction_output` values for a block, recompute `calculate_event_commitment` and `calculate_receipt_commitment` over the collected outputs and compare against the stored header's `event_commitment` and `receipt_commitment`. If they do not match, return `ParseDataError::BadPeer` and report the peer. The `calculate_block_commitments` function already provides the necessary primitives. [10](#0-9) 

### Proof of Concept

1. Set up a P2P sync client with a stored header for block N that has a non-zero `event_commitment`.
2. Send `n_transactions` `FullTransaction` messages where each `transaction_output` has an empty `events` list.
3. Observe that `parse_data_for_block` returns `Ok(Some(...))` and `write_to_storage` commits the body.
4. Read back the stored outputs and call `calculate_event_commitment::<Poseidon>` over them.
5. Assert the result equals `EventCommitment(Felt::ZERO)` (empty tree root), which differs from the header's stored `event_commitment` — confirming the stored data is inconsistent with the committed value.

### Citations

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L36-36)
```rust
            storage_writer.begin_rw_txn()?.append_body(self.1, self.0)?.commit()?;
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L62-66)
```rust
            let target_transaction_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .n_transactions;
```

**File:** crates/apollo_p2p_sync/src/client/transaction.rs (L86-89)
```rust
                block_body.transactions.push(transaction);
                block_body.transaction_outputs.push(transaction_output);
                // TODO(eitan): Validate transaction hash from untrusted sources
                block_body.transaction_hashes.push(transaction_hash);
```

**File:** crates/apollo_storage/src/body/mod.rs (L395-419)
```rust
    fn append_body(self, block_number: BlockNumber, block_body: BlockBody) -> StorageResult<Self> {
        let markers_table = self.open_table(&self.tables.markers)?;
        update_marker(&self.txn, &markers_table, block_number)?;

        if self.scope != StorageScope::StateOnly {
            let events_table = self.open_table(&self.tables.events)?;
            let transaction_hash_to_idx_table =
                self.open_table(&self.tables.transaction_hash_to_idx)?;
            let transaction_metadata_table = self.open_table(&self.tables.transaction_metadata)?;
            let file_offset_table = self.txn.open_table(&self.tables.file_offsets)?;

            write_transactions(
                &block_body,
                &self.txn,
                &self.file_handlers,
                &file_offset_table,
                &transaction_hash_to_idx_table,
                &transaction_metadata_table,
                &events_table,
                block_number,
            )?;
        }

        Ok(self)
    }
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L82-123)
```rust
    fn parse_data_for_block<'a>(
        signed_headers_response_manager: &'a mut ClientResponsesManager<
            DataOrFin<SignedBlockHeader>,
        >,
        block_number: BlockNumber,
        _storage_reader: &'a StorageReader,
    ) -> BoxFuture<'a, Result<Option<Self::Output>, ParseDataError>> {
        async move {
            // TODO(noamsp): investigate and remove this timeout.
            let maybe_signed_header =
                timeout(Duration::from_secs(15), signed_headers_response_manager.next())
                    .await
                    .ok()
                    .flatten()
                    .ok_or(ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }))?;
            let Some(signed_block_header) = maybe_signed_header?.0 else {
                return Ok(None);
            };
            // TODO(shahak): Check that parent_hash is the same as the previous block's hash
            // and handle reverts.
            if block_number
                != signed_block_header.block_header.block_header_without_hash.block_number
            {
                return Err(ParseDataError::BadPeer(BadPeerError::HeadersUnordered {
                    expected_block_number: block_number,
                    actual_block_number: signed_block_header
                        .block_header
                        .block_header_without_hash
                        .block_number,
                }));
            }
            if signed_block_header.signatures.len() != ALLOWED_SIGNATURES_LENGTH {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongSignaturesLength {
                    signatures: signed_block_header.signatures,
                }));
            }
            Ok(Some(signed_block_header))
        }
        .boxed()
    }
```

**File:** crates/starknet_api/src/block_hash/event_commitment.rs (L21-26)
```rust
pub fn calculate_event_commitment<H: StarkHash>(
    event_leaf_elements: &[EventLeafElement],
) -> EventCommitment {
    let event_leaves = event_leaf_elements.iter().map(calculate_event_hash).collect();
    EventCommitment(calculate_root::<H>(event_leaves))
}
```

**File:** crates/starknet_api/src/block_hash/receipt_commitment.rs (L33-39)
```rust
pub fn calculate_receipt_commitment<H: StarkHash>(
    receipt_elements: &[ReceiptElement],
) -> ReceiptCommitment {
    ReceiptCommitment(calculate_root::<H>(
        receipt_elements.iter().map(calculate_receipt_hash).collect(),
    ))
}
```

**File:** crates/apollo_rpc/src/v0_8/api/test.rs (L1392-1402)
```rust
    let output = TransactionOutput::from((
        block.body.transaction_outputs.index(0).clone(),
        transaction_version,
        msg_hash,
    ));
    let expected_receipt = TransactionReceipt {
        finality_status: TransactionFinalityStatus::AcceptedOnL2,
        transaction_hash,
        block_hash: block.header.block_hash,
        block_number: block.header.block_header_without_hash.block_number,
        output,
```

**File:** crates/apollo_p2p_sync/src/client/mod.rs (L114-132)
```rust
        let transaction_stream = TransactionStreamFactory::create_stream(
            self.transaction_sender,
            storage_reader.clone(),
            Some(internal_blocks_receivers.transaction_receiver),
            config.wait_period_for_new_data,
            config.wait_period_for_other_protocol,
            config.num_block_transactions_per_query,
        );

        let class_stream = ClassStreamBuilder::create_stream(
            self.class_sender,
            storage_reader.clone(),
            Some(internal_blocks_receivers.class_receiver),
            config.wait_period_for_new_data,
            config.wait_period_for_other_protocol,
            config.num_block_classes_per_query,
        );

        header_stream.merge(state_diff_stream).merge(transaction_stream).merge(class_stream)
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L285-316)
```rust
pub async fn calculate_block_commitments(
    transactions_data: &[TransactionHashingData],
    state_diff: ThinStateDiff,
    l1_da_mode: L1DataAvailabilityMode,
    starknet_version: &StarknetVersion,
) -> (BlockHeaderCommitments, BlockCommitmentsMeasurements) {
    let transaction_leaf_elements: Vec<TransactionLeafElement> = transactions_data
        .iter()
        .map(|tx_leaf| {
            let mut tx_leaf_element = TransactionLeafElement::from(tx_leaf);
            if starknet_version < &BlockHashVersion::V0_13_4.into()
                && tx_leaf.transaction_signature.0.is_empty()
            {
                tx_leaf_element.transaction_signature =
                    TransactionSignature(vec![Felt::ZERO].into());
            }
            tx_leaf_element
        })
        .collect();

    let event_leaf_elements: Vec<EventLeafElement> = transactions_data
        .iter()
        .flat_map(|transaction_data| {
            transaction_data.transaction_output.events.iter().map(|event| EventLeafElement {
                event: event.clone(),
                transaction_hash: transaction_data.transaction_hash,
            })
        })
        .collect();

    let receipt_elements: Vec<ReceiptElement> =
        transactions_data.iter().map(ReceiptElement::from).collect();
```
