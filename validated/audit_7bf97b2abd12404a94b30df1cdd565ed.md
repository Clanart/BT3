### Title
Unauthenticated `AddGasPrice`/`Initialize` on `RemoteL1GasPriceServer` Allows Injection of Arbitrary L1 Gas Prices into Block Construction — (`crates/apollo_l1_gas_price/src/communication.rs`)

---

### Summary

The `RemoteComponentServer` infrastructure exposes every request variant of `L1GasPriceProvider`—including the state-mutating `Initialize` and `AddGasPrice`—over plain HTTP/2 on `0.0.0.0` with no caller authentication. Any network peer that can reach the internal port can reset the provider's ring buffer and inject arbitrary gas-price samples. The sequencer then uses those attacker-controlled prices when constructing block headers, corrupting the L1 gas price committed into every subsequent block hash and causing wrong fee accounting for all transactions in those blocks.

---

### Finding Description

**Root cause — no authentication in `RemoteComponentServer`.**

`remote_component_server_handler` in `crates/apollo_infra/src/component_server/remote_component_server.rs` deserializes any incoming HTTP/2 body and forwards it to the local component. There is no token, mTLS, IP check, or any other caller-identity mechanism. [1](#0-0) 

**Exposed surface — `L1GasPriceRequest` enum.**

The `L1GasPriceRequest` enum exposes four variants on the same server, including the two state-mutating ones:

```
Initialize          – resets the ring buffer to empty
AddGasPrice(data)   – appends a new GasPriceData sample
GetGasPrice(ts)     – read-only
GetEthToFriRate(ts) – read-only
``` [2](#0-1) 

All four are dispatched without any access check: [3](#0-2) 

**Deployment — server bound to `0.0.0.0`.**

In the production distributed deployment the provider's remote server is configured with `bind_ip: "0.0.0.0"`, making it reachable from any interface on the host: [4](#0-3) 

**Sequential-block-number guard is bypassable after `Initialize`.**

`add_price_info` enforces that each new sample's `block_number` equals `previous + 1`, but only when the ring buffer is non-empty: [5](#0-4) 

After an attacker calls `Initialize`, `samples.back()` returns `None`, so the guard is skipped entirely and the attacker may inject samples starting from any block number with any price. [6](#0-5) 

**Injected prices flow into block hash and fee accounting.**

`get_price_info` returns the mean of the stored samples. The batcher passes this result into `PartialBlockHashComponents::new`, which embeds `l1_gas_price`, `l1_data_gas_price`, and `l2_gas_price` directly into the partial block hash components: [7](#0-6) 

Those components are then committed into the final block hash via `calculate_block_hash`: [8](#0-7) 

`BlockExecutionArtifacts::new` calls `calculate_block_commitments` and `PartialBlockHashComponents::new` using the gas prices from the provider, so every block built after the injection carries the attacker-chosen prices: [9](#0-8) 

---

### Impact Explanation

Wrong L1 gas prices committed into block headers cause:

1. **Incorrect fee bounds checking** — `check_fee_bounds` compares user-supplied resource bounds against the block's gas prices. Artificially low prices allow transactions with insufficient real fees to pass; artificially high prices reject transactions with valid fees.
2. **Wrong fee deduction** — every transaction in the block is charged based on the committed gas price, producing incorrect balance changes for users and the sequencer.
3. **Block hash corruption** — the gas prices are hashed into the block hash via `calculate_block_hash`; a wrong price produces a wrong block hash that diverges from what an honest verifier would compute.

This matches the allowed Critical impact: *"Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact."*

---

### Likelihood Explanation

In the distributed deployment the `l1_gas_price_provider` pod binds to `0.0.0.0`. Any workload in the same Kubernetes cluster (or any host on the same flat network) can open a TCP connection to that port and issue arbitrary `L1GasPriceRequest` messages. A single compromised sidecar, a misconfigured `NetworkPolicy`, or an accidental public exposure of the service port is sufficient to trigger the attack with no credentials required.

---

### Recommendation

Add caller authentication to `RemoteComponentServer` before it is used in production for state-mutating endpoints. Concrete options:

- **mTLS**: require client certificates; the scraper presents its cert, all other callers are rejected.
- **Shared-secret header**: the scraper includes a pre-shared token; the server validates it before forwarding to the local component.
- **Split read/write servers**: expose `GetGasPrice`/`GetEthToFriRate` on one port (wide access) and `Initialize`/`AddGasPrice` on a separate, scraper-only port with IP allowlisting.

Long-term, the `RemoteComponentServer` framework should support a pluggable authentication middleware so every component that exposes mutating operations inherits the protection automatically.

---

### Proof of Concept

```
# Step 1 – reset the ring buffer (clears all legitimate price history)
POST http://<l1_gas_price_provider_host>:<port>/
Content-Type: application/octet-stream
Body: <bincode-serialized L1GasPriceRequest::Initialize>

# Step 2 – inject fake prices (e.g. base_fee = 1 wei, blob_fee = 1 wei)
#           block_number=0 is accepted because the buffer is now empty
POST http://<l1_gas_price_provider_host>:<port>/
Body: <bincode-serialized L1GasPriceRequest::AddGasPrice(GasPriceData {
    block_number: 0,
    timestamp: BlockTimestamp(<current_unix_ts>),
    price_info: PriceInfo { base_fee_per_gas: GasPrice(1), blob_fee: GasPrice(1) }
})>

# Step 3 – repeat with block_number = 1, 2, … until the ring buffer
#           (default 3000 blocks) is filled with attacker-chosen prices.

# Result: the next call to get_price_info() returns mean(1, 1, …) = 1.
# The batcher embeds gas_price=1 into PartialBlockHashComponents,
# calculate_block_hash commits it, and all fee checks in the block
# use the attacker-controlled price.
```

### Citations

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L159-230)
```rust
    #[instrument(skip_all, fields(request_id = %request_id, remote_addr = %client_peer))]
    async fn remote_component_server_handler(
        http_request: HyperRequest<Incoming>,
        request_id: RequestId,
        client_peer: SocketAddr,
        local_client: LocalComponentClient<Request, Response>,
        metrics: &'static RemoteServerMetrics,
    ) -> Result<HyperResponse<Full<Bytes>>, hyper::Error> {
        trace!("Received HTTP request: {http_request:?}");
        let body_bytes = http_request.into_body().collect().await?.to_bytes();
        trace!("Extracted {} bytes from HTTP request body", body_bytes.len());

        metrics.increment_total_received();

        let http_response = match SerdeWrapper::<Request>::wrapper_deserialize(&body_bytes)
            .map_err(|err| ClientError::ResponseDeserializationFailure(err.to_string()))
        {
            Ok(request) => {
                trace!(
                    remote_addr = %client_peer,
                    request_id = %request_id,
                    request_type = request.request_label(),
                    "remote component request",
                );
                trace!("Successfully deserialized request: {request:?}");
                metrics.increment_valid_received();

                // Wrap the send operation in a tokio::spawn as it is NOT a cancel-safe operation.
                // Even if the current task is cancelled, the inner task will continue to run.
                // Note: this creates a new request ID for the local client.
                let response = tokio::spawn(async move { local_client.send(request).await })
                    .await
                    .expect("Should be able to extract value from the task");

                metrics.increment_processed();

                match response {
                    Ok(response) => {
                        trace!("Local client processed request successfully: {response:?}");
                        HyperResponse::builder()
                            .status(StatusCode::OK)
                            .header(CONTENT_TYPE, APPLICATION_OCTET_STREAM)
                            .body(Full::new(Bytes::from(
                                SerdeWrapper::new(response)
                                    .wrapper_serialize()
                                    .expect("Response serialization should succeed"),
                            )))
                    }
                    Err(error) => {
                        panic!(
                            "Remote server failed sending with its local client. Error: {error:?}"
                        );
                    }
                }
            }
            Err(error) => {
                error!("Failed to deserialize request: {error:?}");
                let server_error = ServerError::RequestDeserializationFailure(error.to_string());
                HyperResponse::builder().status(StatusCode::BAD_REQUEST).body(Full::new(
                    Bytes::from(
                        SerdeWrapper::new(server_error)
                            .wrapper_serialize()
                            .expect("Server error serialization should succeed"),
                    ),
                ))
            }
        }
        .expect("Response building should succeed");
        trace!("Built HTTP response: {http_response:?}");

        Ok(http_response)
    }
```

**File:** crates/apollo_l1_gas_price_types/src/lib.rs (L69-74)
```rust
pub enum L1GasPriceRequest {
    Initialize,
    GetGasPrice(BlockTimestamp),
    AddGasPrice(GasPriceData),
    GetEthToFriRate(u64),
}
```

**File:** crates/apollo_l1_gas_price/src/communication.rs (L20-37)
```rust
#[async_trait]
impl ComponentRequestHandler<L1GasPriceRequest, L1GasPriceResponse> for L1GasPriceProvider {
    #[instrument(skip(self))]
    async fn handle_request(&mut self, request: L1GasPriceRequest) -> L1GasPriceResponse {
        match request {
            L1GasPriceRequest::Initialize => L1GasPriceResponse::Initialize(self.initialize()),
            L1GasPriceRequest::GetGasPrice(timestamp) => {
                L1GasPriceResponse::GetGasPrice(self.get_price_info(timestamp))
            }
            L1GasPriceRequest::AddGasPrice(data) => {
                L1GasPriceResponse::AddGasPrice(self.add_price_info(data))
            }
            L1GasPriceRequest::GetEthToFriRate(timestamp) => {
                L1GasPriceResponse::GetEthToFriRate(self.eth_to_fri_rate(timestamp).await)
            }
        }
    }
}
```

**File:** crates/apollo_deployments/resources/services/distributed/l1.json (L55-68)
```json
  "components.l1_gas_price_provider.execution_mode": "LocalExecutionWithRemoteEnabled",
  "components.l1_gas_price_provider.local_server_config.#is_none": false,
  "components.l1_gas_price_provider.local_server_config.high_priority_requests_channel_capacity": 1024,
  "components.l1_gas_price_provider.local_server_config.inbound_requests_channel_capacity": 1024,
  "components.l1_gas_price_provider.local_server_config.normal_priority_requests_channel_capacity": 1024,
  "components.l1_gas_price_provider.local_server_config.processing_time_warning_threshold_ms": 3000,
  "components.l1_gas_price_provider.max_concurrency": 128,
  "components.l1_gas_price_provider.port": 1,
  "components.l1_gas_price_provider.remote_client_config.#is_none": true,
  "components.l1_gas_price_provider.remote_server_config.#is_none": false,
  "components.l1_gas_price_provider.remote_server_config.bind_ip": "0.0.0.0",
  "components.l1_gas_price_provider.remote_server_config.max_streams_per_connection": 8,
  "components.l1_gas_price_provider.remote_server_config.set_tcp_nodelay": true,
  "components.l1_gas_price_provider.url": "remote_service",
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L78-82)
```rust
    pub fn initialize(&mut self) -> L1GasPriceProviderResult<()> {
        info!("Initializing L1GasPriceProvider with config: {:?}", self.config);
        self.price_samples_by_block = Some(RingBuffer::new(self.config.storage_limit));
        Ok(())
    }
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L84-103)
```rust
    pub fn add_price_info(&mut self, new_data: GasPriceData) -> L1GasPriceProviderResult<()> {
        // In case the provider has been restarted while the scraper is still running,
        // a NotInitializedError will be returned to the scraper. We expect the scraper to exit with
        // an error, and that infrastructure will restart it, leading to initialization.
        let Some(samples) = &mut self.price_samples_by_block else {
            return Err(L1GasPriceProviderError::NotInitializedError);
        };
        if let Some(data) = samples.back() {
            if new_data.block_number != data.block_number + 1 {
                return Err(L1GasPriceProviderError::UnexpectedBlockNumberError {
                    expected: data.block_number + 1,
                    found: new_data.block_number,
                });
            }
        }
        trace!("Received price sample for L1 block: {:?}", new_data);
        info_every_n_ms!(1_000, "Received price sample for L1 block: {:?}", new_data);
        samples.push(new_data);
        Ok(())
    }
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L223-236)
```rust
impl PartialBlockHashComponents {
    pub fn new(block_info: &BlockInfo, header_commitments: BlockHeaderCommitments) -> Self {
        Self {
            header_commitments,
            block_number: block_info.block_number,
            l1_gas_price: block_info.gas_prices.l1_gas_price_per_token(),
            l1_data_gas_price: block_info.gas_prices.l1_data_gas_price_per_token(),
            l2_gas_price: block_info.gas_prices.l2_gas_price_per_token(),
            sequencer: SequencerContractAddress(block_info.sequencer_address),
            timestamp: block_info.block_timestamp,
            starknet_version: block_info.starknet_version,
        }
    }
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-282)
```rust
pub fn calculate_block_hash(
    partial_block_hash_components: &PartialBlockHashComponents,
    state_root: GlobalRoot,
    previous_block_hash: BlockHash,
) -> StarknetApiResult<BlockHash> {
    let block_hash_version: BlockHashVersion =
        partial_block_hash_components.starknet_version.try_into()?;
    let block_commitments = &partial_block_hash_components.header_commitments;
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
}
```

**File:** crates/apollo_batcher/src/block_builder.rs (L143-183)
```rust
    pub async fn new(
        BlockExecutionSummary {
            state_diff: commitment_state_diff,
            compressed_state_diff,
            bouncer_weights,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            block_info,
        }: BlockExecutionSummary,
        execution_data: BlockTransactionExecutionData,
        final_n_executed_txs: usize,
    ) -> Self {
        let l1_da_mode = L1DataAvailabilityMode::from_use_kzg_da(block_info.use_kzg_da);
        let transactions_data =
            prepare_txs_hashing_data(&execution_data.execution_infos_and_signatures);
        // TODO(Ayelet): Remove the clones.
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
        record_and_log_block_commitment_measurements(block_info.block_number, measurements);
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
        let l2_gas_used = execution_data.l2_gas_used();
        Self {
            execution_data,
            commitment_state_diff,
            compressed_state_diff,
            bouncer_weights,
            l2_gas_used,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            final_n_executed_txs,
            partial_block_hash_components,
        }
    }
```
