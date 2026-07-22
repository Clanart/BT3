### Title
Unauthenticated `L1ProviderRequest::Initialize` Allows Front-Running to Inject Fake L1 Handler Transactions and Corrupt Block State — (`crates/apollo_l1_provider/src/l1_provider.rs`, `crates/apollo_l1_provider/src/communication.rs`)

---

### Summary

`L1Provider::initialize` accepts any caller without authentication. In the distributed deployment the provider's `RemoteComponentServer` binds to `0.0.0.0` with no TLS or access control. A network-reachable attacker can send `L1ProviderRequest::Initialize` before the legitimate `L1Scraper` does, setting an arbitrary `start_height` and injecting fabricated `L1HandlerTransaction` events. The batcher then pulls those fake transactions via `get_txs`, executes them through the blockifier, and produces a wrong state diff, wrong global root, and wrong block hash. The legitimate scraper's subsequent `initialize` call panics the node, but the corrupted block may already be committed.

---

### Finding Description

`L1Provider::initialize` is the one-time bootstrap call that sets `start_height`, `current_height`, and seeds the `TransactionManager` with the first batch of L1 handler events. Its only guard is a state check:

```rust
// crates/apollo_l1_provider/src/l1_provider.rs  lines 99-126
pub async fn initialize(
    &mut self,
    start_height: BlockNumber,
    events: Vec<Event>,
) -> L1ProviderResult<()> {
    if !self.state.is_uninitialized() {
        panic!("Called initialize while not in Uninitialized state...");
    };
    self.start_height = Some(start_height);
    self.current_height = start_height;
    self.state = ProviderState::Pending;
    self.add_events(events)?;
    Ok(())
}
``` [1](#0-0) 

There is no check on who the caller is. The `ComponentRequestHandler` dispatches `Initialize` identically to every other variant:

```rust
// crates/apollo_l1_provider/src/communication.rs  lines 44-46
L1ProviderRequest::Initialize { historic_l2_height, events } => {
    L1ProviderResponse::Initialize(self.initialize(historic_l2_height, events).await)
}
``` [2](#0-1) 

The `RemoteComponentServer` that wraps this handler performs no authentication — it deserializes any incoming HTTP/2 body and forwards it:

```rust
// crates/apollo_infra/src/component_server/remote_component_server.rs  lines 166-191
let body_bytes = http_request.into_body().collect().await?.to_bytes();
...
let response = tokio::spawn(async move { local_client.send(request).await })
``` [3](#0-2) 

In the distributed and hybrid deployment layouts the L1 Provider is configured with `"remote_server_config.bind_ip": "0.0.0.0"`, making it reachable from any pod or host on the network: [4](#0-3) 

The `L1ProviderRequest` enum exposes `Initialize` as a first-class variant alongside `GetTransactions`, `CommitBlock`, etc., all served over the same unauthenticated port: [5](#0-4) 

---

### Impact Explanation

**Attack path:**

1. Before the `L1Scraper` starts, an attacker sends `L1ProviderRequest::Initialize { historic_l2_height: VERY_HIGH, events: [fake_l1_handler_tx] }` to the provider's remote port.
2. `initialize` succeeds: `start_height = VERY_HIGH`, `current_height = VERY_HIGH`, and the fake `L1HandlerTransaction` is inserted into `TransactionManager`.
3. The batcher calls `start_block(Propose, height)` → `get_txs(n, height)`. The provider returns the attacker-injected fake transaction.
4. The batcher passes it to the blockifier. The blockifier executes the fake L1 handler entry point, producing an attacker-controlled state diff.
5. The commitment manager computes `global_root` from that diff, then `calculate_block_hash` chains it into the block hash:

```rust
// crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs  lines 520-524
let block_hash = calculate_block_hash(
    &partial_block_hash_components,
    global_root,
    previous_block_hash,
)?;
``` [6](#0-5) 

6. The wrong `global_root` and `block_hash` are written to storage via `set_global_root_and_block_hash`.
7. When the legitimate scraper finally calls `initialize`, the provider panics (state is no longer `Uninitialized`), crashing the node — but the corrupted block may already be committed to storage and propagated to consensus.

**Secondary impact — suppression of real L1 messages:** Setting `start_height` to a value above all real L1 handler transactions causes `is_historical_height()` to return `true` for every real `commit_block` call, silently dropping all real L1→L2 messages permanently. [7](#0-6) 

---

### Likelihood Explanation

The attack requires network access to the L1 Provider's remote port before the `L1Scraper` completes its startup sequence (`fetch_start_block` → `get_last_historic_l2_height` → `initialize`). In a Kubernetes deployment with `bind_ip: 0.0.0.0` and no network policy, any pod in the cluster satisfies this precondition. The startup window is deterministic and repeatable: the provider always starts in `Uninitialized` state and the scraper always takes several seconds to query L1 before calling `initialize`. No privileged credentials are required; the attacker only needs TCP connectivity to the provider's port.

---

### Recommendation

1. **Restrict `Initialize` to the local channel only.** Remove `Initialize` from the variants dispatched by `RemoteComponentServer`, or add an allowlist of source addresses. The scraper and provider always co-locate in the same process or pod; there is no legitimate remote caller for `Initialize`.

2. **Validate the caller identity at the application layer.** If remote `Initialize` is ever needed, require a pre-shared token or mTLS certificate checked inside `handle_request` before dispatching `Initialize`.

3. **Replace the panic with a graceful error.** The existing `FIXME` comment acknowledges the panic is wrong. Returning a typed `FatalError` prevents the attacker from using a second `initialize` call as a crash-on-demand primitive.

4. **Validate injected events against on-chain data.** `add_events` accepts any `L1HandlerTransaction` without verifying that the transaction hash corresponds to a real Ethereum log. Cross-checking against the base layer before inserting into `TransactionManager` would limit the damage even if `initialize` is reached by an attacker.

---

### Proof of Concept

```
# Attacker sends Initialize before the scraper, injecting a fake L1 handler tx
# targeting an arbitrary contract with attacker-controlled calldata.

POST http://<l1_provider_host>:<port>/
Content-Type: application/octet-stream
Body: bincode-serialized L1ProviderRequest::Initialize {
    historic_l2_height: BlockNumber(999_999_999),
    events: [
        Event::L1HandlerTransaction {
            l1_handler_tx: L1HandlerTransaction {
                tx: L1HandlerTx {
                    contract_address: <target_contract>,
                    entry_point_selector: <selector>,
                    calldata: <attacker_calldata>,
                    ...
                },
                tx_hash: <any_hash>,
            },
            block_timestamp: ...,
            scrape_timestamp: ...,
        }
    ]
}

# Provider transitions to Pending with start_height=999_999_999 and fake tx in TransactionManager.
# Batcher calls start_block(Propose) → get_txs → receives fake tx.
# Blockifier executes fake L1 handler → wrong state diff → wrong global_root → wrong block_hash.
# Scraper calls initialize → PANIC → node crash.
# Corrupted block already written to storage.
```

### Citations

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L99-126)
```rust
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
```

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L300-309)
```rust
        if self.is_historical_height(height) {
            debug!(
                "Skipping commit block for height: {height}, it is lower than start_height: {}. \
                 Current height is {}.",
                self.start_height
                    .expect("is_historic_height returns false if start_height is not set"),
                self.current_height
            );
            return Ok(());
        }
```

**File:** crates/apollo_l1_provider/src/communication.rs (L44-46)
```rust
            L1ProviderRequest::Initialize { historic_l2_height, events } => {
                L1ProviderResponse::Initialize(self.initialize(historic_l2_height, events).await)
            }
```

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L166-191)
```rust
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
```

**File:** crates/apollo_deployments/resources/services/distributed/l1.json (L70-83)
```json
  "components.l1_provider.execution_mode": "LocalExecutionWithRemoteEnabled",
  "components.l1_provider.local_server_config.#is_none": false,
  "components.l1_provider.local_server_config.high_priority_requests_channel_capacity": 1024,
  "components.l1_provider.local_server_config.inbound_requests_channel_capacity": 1024,
  "components.l1_provider.local_server_config.normal_priority_requests_channel_capacity": 1024,
  "components.l1_provider.local_server_config.processing_time_warning_threshold_ms": 3000,
  "components.l1_provider.max_concurrency": 128,
  "components.l1_provider.port": 1,
  "components.l1_provider.remote_client_config.#is_none": true,
  "components.l1_provider.remote_server_config.#is_none": false,
  "components.l1_provider.remote_server_config.bind_ip": "0.0.0.0",
  "components.l1_provider.remote_server_config.max_streams_per_connection": 8,
  "components.l1_provider.remote_server_config.set_tcp_nodelay": true,
  "components.l1_provider.url": "remote_service",
```

**File:** crates/apollo_l1_provider_types/src/lib.rs (L64-89)
```rust
pub enum L1ProviderRequest {
    AddEvents(Vec<Event>),
    CommitBlock {
        l1_handler_tx_hashes: IndexSet<TransactionHash>,
        rejected_tx_hashes: IndexSet<TransactionHash>,
        height: BlockNumber,
    },
    GetTransactions {
        n_txs: usize,
        height: BlockNumber,
    },
    Initialize {
        historic_l2_height: BlockNumber,
        events: Vec<Event>,
    },
    StartBlock {
        state: SessionState,
        height: BlockNumber,
    },
    Validate {
        tx_hash: TransactionHash,
        height: BlockNumber,
    },
    GetL1ProviderSnapshot,
    GetProviderState,
}
```

**File:** crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs (L520-524)
```rust
                let block_hash = calculate_block_hash(
                    &partial_block_hash_components,
                    global_root,
                    previous_block_hash,
                )?;
```
