### Title
Unauthenticated `add_events()` and `initialize()` on `RemoteL1ProviderServer` Allow Injection of Fake L1 Handler Transactions — (`crates/apollo_l1_provider/src/l1_provider.rs`)

---

### Summary

`L1Provider::add_events()` and `L1Provider::initialize()` are intended to be called exclusively by the `L1Scraper`, but the `RemoteComponentServer` that exposes the `L1Provider` over HTTP/2 in distributed deployments performs no caller authentication. Any network peer who can reach the server's port can call `AddEvents` or `Initialize` with attacker-controlled payloads, injecting fake `L1HandlerTransaction` events into the sequencer's pending transaction pool.

---

### Finding Description

The `L1Provider` component exposes two write operations that are semantically restricted to the `L1Scraper`:

- `add_events()` — accepts `Vec<Event>` and unconditionally adds `L1HandlerTransaction` entries to the internal `TransactionManager` via `tx_manager.add_tx()`.
- `initialize()` — sets `start_height`, transitions state to `Pending`, and calls `add_events()`.

Both are documented as "Functions Called by the scraper" in the source, but neither performs any caller identity check. [1](#0-0) 

The `ComponentRequestHandler` implementation routes `L1ProviderRequest::AddEvents` directly to `add_events()` with no guard: [2](#0-1) 

The `RemoteComponentServer` that wraps this handler is a plain HTTP/2 TCP server. Its `RemoteServerConfig` defaults `bind_ip` to `Ipv4Addr::UNSPECIFIED` (0.0.0.0). The handler deserializes the request body and forwards it to the local client with no authentication, no TLS, and no IP allowlist: [3](#0-2) 

The `RemoteL1ProviderServer` type is defined and used in distributed deployments: [4](#0-3) 

The `L1ProviderClient` trait exposes `add_events` and `initialize` as first-class operations callable by any holder of a client handle: [5](#0-4) 

---

### Impact Explanation

An attacker who can reach the `RemoteL1ProviderServer` port sends an `L1ProviderRequest::AddEvents` message containing one or more crafted `Event::L1HandlerTransaction` entries. These are inserted into `TransactionManager` without validation: [6](#0-5) 

The batcher subsequently calls `get_txs()` and receives the injected transactions. The blockifier executes them, producing wrong state diffs, wrong events, wrong receipts, and wrong L1 message records. The resulting `CommitmentStateDiff` is converted to `ThinStateDiff`, fed into `calculate_block_commitments`, and committed to storage — permanently corrupting the block's state root, event commitment, receipt commitment, and state-diff commitment: [7](#0-6) 

For `initialize()`: if called before the real scraper initializes the provider (i.e., while `state == Uninitialized`), the attacker sets an arbitrary `start_height` and seeds the provider with fake transactions, causing the sequencer to operate from a wrong L2 height baseline. [8](#0-7) 

**Matching impact scope**: *High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing* and *Critical — Wrong state, receipt, event, L1 message, or revert result from execution logic for accepted input.*

---

### Likelihood Explanation

In distributed deployments (`ReactiveComponentExecutionMode::LocalExecutionWithRemoteEnabled`), the `RemoteL1ProviderServer` binds to `0.0.0.0` by default. No firewall rule, TLS certificate, or shared secret is enforced at the application layer. Any host with TCP connectivity to the server's port can exploit this without any credentials or privileged position. [9](#0-8) 

---

### Recommendation

1. **Caller authentication on the remote server**: Add a shared-secret or mTLS layer to `RemoteComponentServer` so that only the authorized `L1Scraper` process can call `AddEvents` and `Initialize`.
2. **Separate the scraper-only API**: Split `L1ProviderRequest` into a scraper-facing variant (containing `AddEvents`, `Initialize`) and a batcher-facing variant (containing `StartBlock`, `GetTransactions`, `CommitBlock`, `Validate`). Expose each on a different port or with different authentication.
3. **Bind to loopback by default**: Change the default `bind_ip` from `UNSPECIFIED` to `127.0.0.1` for internal component servers, requiring explicit opt-in for network exposure.

---

### Proof of Concept

1. Deploy the sequencer in distributed mode with `L1Provider` running as a remote component.
2. Identify the `RemoteL1ProviderServer` port from the node configuration.
3. Craft a binary-serialized `SerdeWrapper<L1ProviderRequest::AddEvents(vec![Event::L1HandlerTransaction { l1_handler_tx: <crafted_tx>, ... }])>` payload.
4. Send it as an HTTP/2 POST to the server's port with the `REQUEST_ID_HEADER` set to any valid `u64`.
5. Observe that the crafted `L1HandlerTransaction` is accepted into the provider's `TransactionManager` and subsequently proposed in the next block by the batcher, producing a wrong state diff and wrong block commitment. [10](#0-9) [11](#0-10)

### Citations

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L96-196)
```rust
    // Functions Called by the scraper.

    // Start the provider, get first-scrape events, start L2 sync.
    pub async fn initialize(
        &mut self,
        start_height: BlockNumber,
        events: Vec<Event>,
    ) -> L1ProviderResult<()> {
        info!("Initializing l1 provider");
        if !self.state.is_uninitialized() {
            // FIXME: This should be return FatalError or similar, which should trigger a planned
            // restart from the infra, since this CAN happen if the scraper recovered from a crash.
            // Right now this is effectively a KILL message when called in steady state.
            panic!(
                "Called initialize while not in Uninitialized state. Restart service. Provider \
                 state: {:?}",
                self.state
            );
        };

        // The provider now goes into Pending state.
        // The current_height is set to a very old height, that doesn't include any of the events
        // sent now, or to be scraped in the future. The provider will begin catching up when the
        // batcher calls commit_block with a height above the current height.
        self.start_height = Some(start_height);
        self.current_height = start_height;
        self.state = ProviderState::Pending;
        self.add_events(events)?;

        Ok(())
    }

    /// Accept new events from the scraper.
    #[instrument(skip_all, err)]
    pub fn add_events(&mut self, events: Vec<Event>) -> L1ProviderResult<()> {
        if self.state.is_uninitialized() {
            return Err(L1ProviderError::Uninitialized);
        }

        // TODO(guyn): can we remove this "every sec" since the polling interval is rather long?
        info_every_n_ms!(1000, "Adding {} l1 events", events.len());
        trace!("Adding events: {events:?}");

        for event in events {
            match event {
                Event::L1HandlerTransaction {
                    l1_handler_tx,
                    block_timestamp,
                    scrape_timestamp,
                } => {
                    self.tx_manager.add_tx(l1_handler_tx, block_timestamp, scrape_timestamp);
                }
                Event::TransactionCancellationStarted {
                    tx_hash,
                    cancellation_request_timestamp,
                } => {
                    if !self.tx_manager.exists(tx_hash) {
                        warn!(
                            "Dropping cancellation request for old L1 handler transaction \
                             {tx_hash}: not in the provider and will never be scraped at this \
                             point."
                        );
                        continue;
                    }

                    self.tx_manager
                        .request_cancellation(tx_hash, cancellation_request_timestamp)
                        .inspect(|previous_request_timestamp| {
                            // Re-requesting a cancellation is meaningful for the L1 timelock, but
                            // for the l2 timelock we only consider the first cancellation
                            // relevant.
                            info!(
                                "Dropping duplicated cancellation request for {tx_hash} at \
                                 {cancellation_request_timestamp}, previous request block \
                                 timestamp still stands: {previous_request_timestamp}"
                            );
                        });
                }
                Event::TransactionCanceled { tx_hash } => {
                    info!(
                        "Cancellation finalized for tx_hash: {tx_hash}. Deleting the tx from the \
                         provider records."
                    );
                    self.tx_manager.finalize_cancellation(tx_hash);
                }
                Event::TransactionConsumed { tx_hash, timestamp: consumed_at } => {
                    if let Err(previously_consumed_at) =
                        self.tx_manager.consume_tx(tx_hash, consumed_at, self.clock.unix_now())
                    {
                        // TODO(guyn): need to check if this is really a critical bug, or if we can
                        // log and ignore.
                        panic!(
                            "Double consumption of {tx_hash} at {consumed_at}, previously \
                             consumed at {previously_consumed_at}."
                        );
                    }
                }
            }
        }
        Ok(())
    }
```

**File:** crates/apollo_l1_provider/src/communication.rs (L8-13)
```rust
pub type LocalL1ProviderServer =
    LocalComponentServer<L1Provider, L1ProviderRequest, L1ProviderResponse>;
pub type RemoteL1ProviderServer = RemoteComponentServer<L1ProviderRequest, L1ProviderResponse>;
pub type L1ProviderRequestWrapper = RequestWrapper<L1ProviderRequest, L1ProviderResponse>;
pub type LocalL1ProviderClient = LocalComponentClient<L1ProviderRequest, L1ProviderResponse>;
pub type RemoteL1ProviderClient = RemoteComponentClient<L1ProviderRequest, L1ProviderResponse>;
```

**File:** crates/apollo_l1_provider/src/communication.rs (L24-27)
```rust
        match request {
            L1ProviderRequest::AddEvents(events) => {
                L1ProviderResponse::AddEvents(self.add_events(events))
            }
```

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L117-127)
```rust
impl Default for RemoteServerConfig {
    fn default() -> Self {
        Self {
            max_streams_per_connection: DEFAULT_MAX_STREAMS_PER_CONNECTION,
            bind_ip: DEFAULT_BIND_IP,
            set_tcp_nodelay: true,
            keepalive_interval_ms: DEFAULT_KEEPALIVE_INTERVAL_MS,
            keepalive_timeout_ms: DEFAULT_KEEPALIVE_TIMEOUT_MS,
        }
    }
}
```

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

**File:** crates/apollo_l1_provider_types/src/lib.rs (L111-144)
```rust
pub trait L1ProviderClient: Send + Sync {
    async fn start_block(
        &self,
        state: SessionState,
        height: BlockNumber,
    ) -> L1ProviderClientResult<()>;

    async fn get_txs(
        &self,
        n_txs: usize,
        height: BlockNumber,
    ) -> L1ProviderClientResult<Vec<L1HandlerTransaction>>;

    async fn validate(
        &self,
        _tx_hash: TransactionHash,
        _height: BlockNumber,
    ) -> L1ProviderClientResult<ValidationStatus>;

    async fn commit_block(
        &self,
        l1_handler_consumed_tx_hashes: IndexSet<TransactionHash>,
        l1_handler_rejected_tx_hashes: IndexSet<TransactionHash>,
        height: BlockNumber,
    ) -> L1ProviderClientResult<()>;

    async fn add_events(&self, events: Vec<Event>) -> L1ProviderClientResult<()>;
    async fn initialize(
        &self,
        historic_l2_height: BlockNumber,
        events: Vec<Event>,
    ) -> L1ProviderClientResult<()>;
    async fn get_l1_provider_snapshot(&self) -> L1ProviderClientResult<L1ProviderSnapshot>;
    async fn get_provider_state(&self) -> L1ProviderClientResult<ProviderState>;
```

**File:** crates/apollo_l1_provider_types/src/lib.rs (L224-236)
```rust
    #[instrument(skip(self))]
    async fn add_events(&self, events: Vec<Event>) -> L1ProviderClientResult<()> {
        let request = L1ProviderRequest::AddEvents(events);
        handle_all_response_variants!(
            self,
            request,
            L1ProviderResponse,
            AddEvents,
            L1ProviderClientError,
            L1ProviderError,
            Direct
        )
    }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L155-183)
```rust
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
