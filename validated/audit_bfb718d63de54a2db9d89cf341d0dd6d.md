### Title
Unauthenticated `SetProof` on `RemoteProofManagerServer` Allows Pre-Poisoning the Proof Cache, Bypassing Cryptographic Proof Verification for Invoke V3 Transactions - (File: `crates/apollo_proof_manager/src/proof_manager.rs`, `crates/apollo_transaction_converter/src/transaction_converter.rs`)

---

### Summary

`ProofManager::set_proof()` is exposed over an unauthenticated `RemoteComponentServer` bound to `0.0.0.0`. The proof-verification path in `run_proof_verification()` skips cryptographic verification entirely when `contains_proof()` returns `true`. An attacker who pre-stores any bytes (including garbage) under a target `proof_facts` hash causes the sequencer to accept Invoke V3 transactions with unverified proofs, bypassing the client-side proving security guarantee.

---

### Finding Description

**Root cause — `ProofManager::set_proof` has no caller check and silently accepts the first write:** [1](#0-0) 

`set_proof` checks whether the key already exists and, if so, returns `Ok(())` without overwriting. The first caller wins, permanently. There is no authentication on who may call this function.

**Root cause — `run_proof_verification` skips cryptographic verification when the key is already present:** [2](#0-1) 

If `contains_proof` returns `true`, the function returns `Ok(false)` — the `starknet_proof_verifier::verify_proof` call is never reached. Both the gateway flow (`spawn_proof_verification`) and the consensus flow (`spawn_verify_and_store_proof`) rely on this shared helper. [3](#0-2) 

**Root cause — `RemoteComponentServer` has no authentication:**

The `RemoteComponentServer` handler deserializes the request body and dispatches it directly to the local client. There is no token, TLS client certificate, IP allowlist, or any other caller-identity check. [4](#0-3) 

**Root cause — the proof manager's remote server is bound to `0.0.0.0` in the distributed deployment:** [5](#0-4) 

`SetProof`, `GetProof`, and `ContainsProof` are all exposed over this unauthenticated HTTP/2 endpoint. [6](#0-5) 

**The blockifier's `validate_proof_facts` does NOT re-verify the cryptographic proof:**

`perform_pre_validation_stage` calls `validate_proof_facts`, which only checks the block number range, the stored block hash, the allowed program hash, and the config hash. It never calls `verify_proof`. [7](#0-6) 

Therefore, once the proof manager cache is poisoned, no downstream check recovers the invariant.

---

### Impact Explanation

An attacker pre-stores arbitrary bytes under a target `proof_facts` hash. Every subsequent Invoke V3 transaction carrying those `proof_facts` is accepted by the sequencer without any cryptographic proof verification. The account can claim any historical SNOS execution output (arbitrary block hash, program hash, config hash) without actually proving it. This satisfies the **Critical** impact: *"Invalid or unauthorized Starknet transaction accepted through account validation … logic."*

---

### Likelihood Explanation

- The `proof_facts` field is public in the transaction (visible in the mempool or predictable from the account's intended block reference).
- The `proof_facts.hash()` is a deterministic Poseidon hash of the public field, computable by anyone.
- The proof manager's remote server is bound to `0.0.0.0` with no authentication in the reference distributed deployment.
- A single HTTP/2 POST with a serialized `SetProof(proof_facts, [0u8])` request permanently poisons the cache entry.
- The attack is a one-shot, low-cost, network-reachable operation requiring no privileged access.

---

### Recommendation

1. **Add caller authentication to `RemoteComponentServer`** (e.g., mutual TLS, a shared secret header, or IP allowlisting) so that only trusted internal components can call `SetProof`.
2. **Never skip cryptographic verification based solely on cache presence.** `run_proof_verification` should verify the proof even when `contains_proof` returns `true`, or at minimum verify before the first `set_proof` write and reject writes that fail verification.
3. **Validate the proof in `set_proof` itself** before persisting it, so the proof manager never stores an unverified proof regardless of the caller.

---

### Proof of Concept

```
// Attacker observes a pending Invoke V3 tx with proof_facts = F in the mempool.
// Attacker computes facts_hash = Poseidon(F) (public computation).

// Step 1: Attacker sends to proof_manager:port (0.0.0.0, no auth):
POST / HTTP/2
Content-Type: application/octet-stream
Body: bincode(ProofManagerRequest::SetProof(F, Proof(vec![0u8])))

// ProofManager::set_proof:
//   contains_proof(F) -> false  (first write)
//   proof_storage.set_proof(facts_hash, Proof([0u8]))  <- garbage stored
//   cache.insert(facts_hash, Proof([0u8]))

// Step 2: Legitimate user submits Invoke V3 tx with proof_facts = F and valid proof P.
// Gateway calls run_proof_verification(F, P, proof_manager_client):
//   contains_proof(F) -> true   (attacker's garbage is present)
//   return Ok(false)            <- cryptographic verify_proof(F, P) NEVER CALLED

// Step 3: Transaction passes validate_proof_facts in blockifier
//   (checks block hash / program hash / config hash only, not the proof bytes)
//   -> transaction accepted and executed with unverified proof.
``` [1](#0-0) [8](#0-7) [9](#0-8)

### Citations

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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L398-407)
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
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L446-470)
```rust
    fn spawn_verify_and_store_proof(
        &self,
        proof_facts: ProofFacts,
        proof: Proof,
    ) -> VerifyAndStoreProofTask {
        let pmc = self.proof_manager_client.clone();
        let proof_facts_hash = proof_facts.hash();
        tokio::spawn(async move {
            let verified =
                Self::run_proof_verification(proof_facts.clone(), proof.clone(), pmc.clone())
                    .await?;

            if !verified {
                return Ok(());
            }

            let start = Instant::now();
            pmc.set_proof(proof_facts, proof).await?;
            let duration = start.elapsed();
            CONSENSUS_PROOF_MANAGER_STORE_LATENCY.record(duration.as_secs_f64());
            info!(
                "Proof manager store took: {duration:?} for proof facts hash: {proof_facts_hash:?}"
            );
            Ok(())
        })
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

**File:** crates/apollo_deployments/resources/services/distributed/proof_manager.json (L78-91)
```json
  "components.proof_manager.execution_mode": "LocalExecutionWithRemoteEnabled",
  "components.proof_manager.local_server_config.#is_none": false,
  "components.proof_manager.local_server_config.high_priority_requests_channel_capacity": 1024,
  "components.proof_manager.local_server_config.inbound_requests_channel_capacity": 1024,
  "components.proof_manager.local_server_config.normal_priority_requests_channel_capacity": 1024,
  "components.proof_manager.local_server_config.processing_time_warning_threshold_ms": 3000,
  "components.proof_manager.max_concurrency": 128,
  "components.proof_manager.port": 1,
  "components.proof_manager.remote_client_config.#is_none": true,
  "components.proof_manager.remote_server_config.#is_none": false,
  "components.proof_manager.remote_server_config.bind_ip": "0.0.0.0",
  "components.proof_manager.remote_server_config.max_streams_per_connection": 8,
  "components.proof_manager.remote_server_config.set_tcp_nodelay": true,
  "components.proof_manager.url": "remote_service",
```

**File:** crates/apollo_proof_manager/src/communication.rs (L13-32)
```rust
impl ComponentRequestHandler<ProofManagerRequest, ProofManagerResponse> for ProofManager {
    async fn handle_request(&mut self, request: ProofManagerRequest) -> ProofManagerResponse {
        match request {
            ProofManagerRequest::SetProof(proof_facts, proof) => ProofManagerResponse::SetProof(
                self.set_proof(proof_facts, proof)
                    .await
                    .map_err(|e| ProofManagerError::ProofStorage(e.to_string())),
            ),
            ProofManagerRequest::GetProof(proof_facts) => ProofManagerResponse::GetProof(
                self.get_proof(proof_facts)
                    .await
                    .map_err(|e| ProofManagerError::ProofStorage(e.to_string())),
            ),
            ProofManagerRequest::ContainsProof(proof_facts) => ProofManagerResponse::ContainsProof(
                self.contains_proof(proof_facts)
                    .await
                    .map_err(|e| ProofManagerError::ProofStorage(e.to_string())),
            ),
        }
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
