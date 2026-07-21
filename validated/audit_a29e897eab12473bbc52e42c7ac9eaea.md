### Title
Unauthenticated `SetProof` on `RemoteComponentServer` Allows Pre-Poisoning of Proof Cache, Bypassing Cryptographic Proof Verification for Invoke V3 Transactions - (`crates/apollo_proof_manager/src/communication.rs`, `crates/apollo_transaction_converter/src/transaction_converter.rs`)

---

### Summary

The `ProofManager`'s `SetProof` RPC endpoint is exposed via `RemoteComponentServer` bound to `0.0.0.0` with no authentication. Any network peer can call `SetProof(valid_proof_facts, garbage_proof_bytes)` directly. Because `run_proof_verification` unconditionally skips cryptographic verification when `contains_proof` returns `true`, an attacker who pre-populates the proof store with garbage bytes for a given `proof_facts` key causes the gateway to accept a subsequent Invoke V3 transaction carrying those same `proof_facts` and the same garbage proof — without ever running `starknet_proof_verifier::verify_proof`. The transaction is then admitted to the mempool and executed.

---

### Finding Description

**Root cause — unauthenticated write endpoint:**

`ProofManager` implements `ComponentRequestHandler` and handles `ProofManagerRequest::SetProof` without any caller identity check:

```rust
// crates/apollo_proof_manager/src/communication.rs
ProofManagerRequest::SetProof(proof_facts, proof) => ProofManagerResponse::SetProof(
    self.set_proof(proof_facts, proof).await ...
),
``` [1](#0-0) 

`ProofManager::set_proof` stores whatever bytes are supplied, keyed by `proof_facts.hash()`, with no cryptographic check on the proof content:

```rust
pub async fn set_proof(&self, proof_facts: ProofFacts, proof: Proof) -> ... {
    if self.contains_proof(proof_facts.clone()).await? { return Ok(()); }
    let facts_hash = proof_facts.hash();
    self.proof_storage.set_proof(facts_hash, proof.clone()).await?;
    self.cache.insert(facts_hash, proof);
    Ok(())
}
``` [2](#0-1) 

This handler is served by a `RemoteComponentServer` bound to `0.0.0.0` with no TLS, no token, and no IP allowlist: [3](#0-2) 

The `RemoteComponentServer` HTTP handler performs no authentication before forwarding the deserialized request to the local client: [4](#0-3) 

**Root cause — verification skip on cache hit:**

`run_proof_verification` in `TransactionConverter` checks the proof manager cache first and returns early — skipping `starknet_proof_verifier::verify_proof` entirely — if the key is already present:

```rust
let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;
if contains_proof {
    return Ok(false);  // verification skipped
}
// ... starknet_proof_verifier::verify_proof only reached here
``` [5](#0-4) 

**Attack path:**

1. Attacker constructs `proof_facts` with valid metadata fields: a `program_hash` from `allowed_virtual_os_program_hashes`, the real `block_hash` for block N (public on-chain), the correct `block_number`, and the correct `config_hash` (deterministic from chain config). These fields are all publicly derivable.
2. Attacker sends `SetProof(valid_proof_facts, garbage_bytes)` directly to the proof manager's HTTP port (bound to `0.0.0.0`). The proof manager stores the garbage bytes under `hash(valid_proof_facts)`.
3. Attacker submits an Invoke V3 transaction to the gateway with the same `valid_proof_facts` and the same `garbage_bytes` as the proof field.
4. Gateway calls `convert_rpc_tx_to_internal_rpc_tx` → `spawn_proof_verification` → `run_proof_verification`. `contains_proof` returns `true` (attacker pre-populated it). Verification is skipped; the task returns `Ok(false)`.
5. `await_verification_task_and_extract_proof_data` sees no error and returns `Some((valid_proof_facts, garbage_bytes))`.
6. `store_proof_and_spawn_archiving` stores the garbage proof in the proof manager (already present, so no-op) and proceeds.
7. Blockifier's `validate_proof_facts` checks only metadata fields (program hash, block hash, block number, config hash) — it does **not** verify the proof bytes.
8. Transaction is admitted to the mempool and executed. [6](#0-5) [7](#0-6) 

The production deployment config explicitly enables `allow_client_side_proving: true`, making this code path active: [8](#0-7) 

---

### Impact Explanation

A transaction carrying an invalid (garbage) SNOS proof is accepted by the gateway and admitted to the mempool. The cryptographic invariant that every client-side-proven Invoke V3 transaction must carry a valid SNOS proof is broken. This matches the allowed impact: **"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."**

The corrupted value is the proof stored under `hash(proof_facts)` in `FsProofStorage` / `ProofCache`. Once written, it permanently poisons that cache slot: any future legitimate transaction with the same `proof_facts` also skips verification (the `set_proof` early-return on `contains_proof` prevents overwriting). [9](#0-8) 

---

### Likelihood Explanation

- The proof manager's remote server is bound to `0.0.0.0` in the distributed deployment configuration, making it reachable from any pod in the same Kubernetes cluster or any host with network access to the service port.
- No authentication, TLS client certificate, or IP allowlist is configured on the `RemoteComponentServer`.
- All inputs needed to construct valid `proof_facts` (program hash, block hash, block number, config hash) are publicly observable on-chain or from the node's versioned constants.
- The attacker needs only one successful `SetProof` call per `proof_facts` key to permanently poison that slot.

---

### Recommendation

1. **Restrict `SetProof` to internal callers only.** The proof manager's remote server should not expose `SetProof` externally. Either bind it to `127.0.0.1` / a cluster-internal interface, or add an allowlist of authorized caller IPs/identities.
2. **Verify the proof before storing it.** `ProofManager::set_proof` should call `starknet_proof_verifier::verify_proof(proof_facts, proof)` before persisting, so the store can never contain an invalid proof regardless of caller.
3. **Do not skip verification based solely on cache presence.** The `contains_proof` early-return in `run_proof_verification` is safe only if the store is trusted. If the store can be written by untrusted parties, the skip must be removed or the stored proof must be re-verified on retrieval.

---

### Proof of Concept

```
# Step 1: Pre-poison the proof manager with garbage proof bytes
# proof_facts = [PROOF0, VIRTUAL_SNOS, 1, VIRTUAL_SNOS0, <block_number>,
#                <window_size>, <real_block_hash_for_block_N>, <config_hash>]
# (all values are publicly observable)

curl -X POST http://<proof_manager_host>:<port>/ \
  -H "Content-Type: application/octet-stream" \
  -H "X-Request-Id: 1" \
  --data-binary @<serialized_SetProof(valid_proof_facts, b"\x00\x00\x00\x01")>

# Step 2: Submit Invoke V3 to gateway with same proof_facts and garbage proof
# Gateway calls run_proof_verification:
#   contains_proof(valid_proof_facts) -> true  (attacker pre-populated)
#   -> verification skipped, Ok(false) returned
#   -> transaction admitted to mempool without proof verification
```

The serialized `SetProof` request uses the same `SerdeWrapper` binary format as all other `ProofManagerRequest` variants, which is straightforward to construct from the public `ProofManagerRequest` enum definition. [10](#0-9)

### Citations

**File:** crates/apollo_proof_manager/src/communication.rs (L14-20)
```rust
    async fn handle_request(&mut self, request: ProofManagerRequest) -> ProofManagerResponse {
        match request {
            ProofManagerRequest::SetProof(proof_facts, proof) => ProofManagerResponse::SetProof(
                self.set_proof(proof_facts, proof)
                    .await
                    .map_err(|e| ProofManagerError::ProofStorage(e.to_string())),
            ),
```

**File:** crates/apollo_proof_manager/src/proof_manager.rs (L54-66)
```rust
    pub async fn set_proof(
        &self,
        proof_facts: ProofFacts,
        proof: Proof,
    ) -> Result<(), FsProofStorageError> {
        if self.contains_proof(proof_facts.clone()).await? {
            return Ok(());
        }
        let facts_hash = proof_facts.hash();
        self.proof_storage.set_proof(facts_hash, proof.clone()).await?;
        self.cache.insert(facts_hash, proof);
        Ok(())
    }
```

**File:** crates/apollo_deployments/resources/services/distributed/proof_manager.json (L86-90)
```json
  "components.proof_manager.remote_client_config.#is_none": true,
  "components.proof_manager.remote_server_config.#is_none": false,
  "components.proof_manager.remote_server_config.bind_ip": "0.0.0.0",
  "components.proof_manager.remote_server_config.max_streams_per_connection": 8,
  "components.proof_manager.remote_server_config.set_tcp_nodelay": true,
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L398-424)
```rust
    async fn run_proof_verification(
        proof_facts: ProofFacts,
        proof: Proof,
        proof_manager_client: SharedProofManagerClient,
    ) -> Result<bool, TransactionConverterError> {
        let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;

        if contains_proof {
            return Ok(false);
        }

        let proof_facts_hash = proof_facts.hash();
        let verify_start = Instant::now();
        tokio::task::spawn_blocking(move || {
            starknet_proof_verifier::verify_proof(proof_facts, proof)
        })
        .await
        .expect("proof verification task panicked")?;
        let verify_duration = verify_start.elapsed();
        PROOF_VERIFICATION_LATENCY.record(verify_duration.as_secs_f64());
        info!(
            "Proof verification took: {verify_duration:?} for proof facts hash: \
             {proof_facts_hash:?}"
        );

        Ok(true)
    }
```

**File:** crates/apollo_gateway/src/gateway.rs (L181-239)
```rust
    async fn add_tx_inner(
        &self,
        tx: RpcTransaction,
        p2p_message_metadata: Option<BroadcastedMessageMetadata>,
    ) -> GatewayResult<GatewayOutput> {
        let mut metric_counters = GatewayMetricHandle::new(&tx, &p2p_message_metadata);
        metric_counters.count_transaction_received();

        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }

        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;

        let tx_signature = tx.signature().clone();
        let (internal_tx, executable_tx, proof_data) =
            self.convert_rpc_tx_to_internal_and_executable_txs(tx, &tx_signature).await?;

        let mut stateful_transaction_validator = self
            .stateful_tx_validator_factory
            .instantiate_validator()
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let nonce = stateful_transaction_validator
            .extract_state_nonce_and_run_validations(&executable_tx, self.mempool_client.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let proof_archive_handle =
            self.store_proof_and_spawn_archiving(proof_data, internal_tx.tx_hash).await;

        let gateway_output = create_gateway_output(&internal_tx);

        let add_tx_args = AddTransactionArgsWrapper {
            args: AddTransactionArgs::new(internal_tx, nonce),
            p2p_message_metadata,
        };
        let mempool_client_result = self.mempool_client.add_tx(add_tx_args).await;
        match mempool_client_result_to_deprecated_gw_result(&tx_signature, mempool_client_result) {
            Ok(()) => {}
            Err(e) => {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        };

        metric_counters.transaction_sent_to_mempool();

        // We await proof archiving only after the transaction is sent to the mempool to avoid
        // delays.
        Self::await_proof_archiving(proof_archive_handle).await;

        Ok(gateway_output)
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L291-347)
```rust
    fn validate_proof_facts(
        &self,
        block_context: &BlockContext,
        state: &mut dyn State,
    ) -> TransactionPreValidationResult<()> {
        // Only Invoke V3 transactions can carry proof facts.
        let Transaction::Invoke(invoke_tx) = &self.tx else {
            return Ok(());
        };
        if invoke_tx.version() < TransactionVersion::THREE {
            return Ok(());
        }

        // Parse proof facts.
        let proof_facts = invoke_tx.proof_facts();
        let snos_proof_facts = match ProofFactsVariant::try_from(&proof_facts)
            .map_err(|e| TransactionPreValidationError::InvalidProofFacts(e.to_string()))?
        {
            ProofFactsVariant::Empty => return Ok(()),
            ProofFactsVariant::Snos(snos_proof_facts) => snos_proof_facts,
        };
        let os_constants = &block_context.versioned_constants.os_constants;

        // Validate the program hash.
        let allowed = &os_constants.allowed_virtual_os_program_hashes;
        if !allowed.contains(&snos_proof_facts.program_hash) {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS program hash {} is not allowed",
                snos_proof_facts.program_hash
            )));
        }

        // Validate the block hash and block number.
        let proof_block_hash = snos_proof_facts.block_hash.0;
        let proof_block_number = snos_proof_facts.block_number.0;
        Self::validate_proof_block_number(
            proof_block_number,
            block_context.block_info.block_number,
        )?;
        Self::validate_proof_block_hash(proof_block_hash, proof_block_number, os_constants, state)?;

        // Validate the config hash.
        let chain_info = &block_context.chain_info;
        // TODO(Meshi): Cache this computation as part of the chain context.
        let virtual_os_config_hash = OsChainInfo::from(chain_info)
            .compute_virtual_os_config_hash()
            .expect("Failed to compute OS config hash");
        let proof_config_hash = snos_proof_facts.config_hash;
        if virtual_os_config_hash != proof_config_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS config hash mismatch. Computed virtual OS config hash: \
                 {virtual_os_config_hash}, expected virtual OS config hash: {proof_config_hash}."
            )));
        }

        Ok(())
    }
```

**File:** crates/apollo_deployments/resources/app_configs/gateway_config.json (L20-20)
```json
  "gateway_config.static_config.stateless_tx_validator_config.allow_client_side_proving": true,
```

**File:** crates/apollo_proof_manager_types/src/lib.rs (L64-77)
```rust
#[derive(Clone, Serialize, Deserialize, AsRefStr, EnumDiscriminants)]
#[strum_discriminants(
    name(ProofManagerRequestLabelValue),
    derive(IntoStaticStr, EnumIter, VariantNames),
    strum(serialize_all = "snake_case")
)]
pub enum ProofManagerRequest {
    SetProof(ProofFacts, Proof),
    GetProof(ProofFacts),
    ContainsProof(ProofFacts),
}
impl_debug_for_infra_requests_and_responses!(ProofManagerRequest);
impl_labeled_request!(ProofManagerRequest, ProofManagerRequestLabelValue);
impl PrioritizedRequest for ProofManagerRequest {}
```
