### Title
Unauthenticated `SetProof` on `RemoteProofManagerServer` Allows Proof-Verification Bypass, Enabling Admission of Transactions with Unverified ZK Proofs - (File: `crates/apollo_proof_manager/src/communication.rs`)

---

### Summary

The `RemoteProofManagerServer` exposes `SetProof`, `GetProof`, and `ContainsProof` over HTTP/2 bound to `0.0.0.0` with no caller authentication. Because `TransactionConverter::run_proof_verification` unconditionally skips ZK-proof verification when `contains_proof` returns `true`, any network-reachable peer can pre-populate the proof manager with an arbitrary (unverified) proof keyed to attacker-chosen `ProofFacts`. A subsequent Invoke V3 transaction carrying those same `ProofFacts` will then pass through the gateway and consensus converter without its ZK proof ever being verified, allowing a transaction backed by a fabricated proof to be admitted into the sequencer.

---

### Finding Description

**Root cause — no caller restriction on `handle_request`:**

`ProofManager::handle_request` dispatches all three variants (`SetProof`, `GetProof`, `ContainsProof`) without any identity or origin check:

```rust
// crates/apollo_proof_manager/src/communication.rs  lines 13-32
impl ComponentRequestHandler<ProofManagerRequest, ProofManagerResponse> for ProofManager {
    async fn handle_request(&mut self, request: ProofManagerRequest) -> ProofManagerResponse {
        match request {
            ProofManagerRequest::SetProof(proof_facts, proof) =>
                ProofManagerResponse::SetProof(self.set_proof(proof_facts, proof).await ...),
            ...
        }
    }
}
``` [1](#0-0) 

`ProofManager::set_proof` itself performs no proof validity check — it stores whatever bytes are supplied:

```rust
// crates/apollo_proof_manager/src/proof_manager.rs  lines 54-66
pub async fn set_proof(&self, proof_facts: ProofFacts, proof: Proof) -> Result<(), ...> {
    if self.contains_proof(proof_facts.clone()).await? { return Ok(()); }
    let facts_hash = proof_facts.hash();
    self.proof_storage.set_proof(facts_hash, proof.clone()).await?;
    self.cache.insert(facts_hash, proof);
    Ok(())
}
``` [2](#0-1) 

**The server is bound to all interfaces with no authentication:**

The `RemoteComponentServer` handler (`remote_component_server_handler`) reads the body, deserializes the request, and forwards it — there is no IP allowlist, token, or TLS-client-certificate check: [3](#0-2) 

The production deployment config confirms `bind_ip: "0.0.0.0"`: [4](#0-3) 

**The verification skip — the exploitable invariant:**

`run_proof_verification` skips the actual `starknet_proof_verifier::verify_proof` call whenever the proof is already present in the proof manager:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs  lines 398-407
async fn run_proof_verification(...) -> Result<bool, ...> {
    let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;
    if contains_proof {
        return Ok(false);   // ← verification skipped entirely
    }
    // ... starknet_proof_verifier::verify_proof(proof_facts, proof) ...
}
``` [5](#0-4) 

This logic is shared by both the gateway flow (`spawn_proof_verification`) and the consensus flow (`spawn_verify_and_store_proof`). [6](#0-5) 

---

### Impact Explanation

An attacker who can reach the proof-manager port (network-adjacent or, in misconfigured deployments, internet-facing) can:

1. Craft structurally valid `ProofFacts` (correct `proof_version`, an allowed `program_hash`, a real stored `block_hash` for a sufficiently old block, and the correct `config_hash`).
2. Call `SetProof` on the `RemoteProofManagerServer` with those `ProofFacts` and an arbitrary (invalid) `Proof` blob.
3. Submit an Invoke V3 transaction through the public gateway carrying the same `ProofFacts`.
4. The gateway's `run_proof_verification` finds `contains_proof == true` and returns `Ok(false)` — the ZK proof is never verified.
5. The blockifier's `validate_proof_facts` still checks the structural fields (block hash against on-chain storage, program hash against allowlist, config hash), all of which pass because the attacker used real values.
6. The transaction is admitted, sequenced, and executed with a proof that was never cryptographically verified.

This matches the **High** impact scope: *"Mempool/gateway/RPC admission accepts invalid transactions … before sequencing."* [7](#0-6) 

---

### Likelihood Explanation

- The `RemoteProofManagerServer` is a production deployment artifact (present in `distributed/proof_manager.json` and `distributed/replacer_proof_manager.json`), not a test fixture.
- The bind address `0.0.0.0` means any host that can reach the proof-manager port can send `SetProof` requests.
- No credentials, tokens, or mutual TLS are configured anywhere in the `RemoteComponentServer` infrastructure.
- The attacker only needs to know the port and the serialization format (`SerdeWrapper` over bincode/serde), both of which are deterministic from the public codebase.
- The structural `ProofFacts` fields required to pass blockifier validation are all derivable from public chain data (block hashes are public; allowed program hashes and config hash are in `versioned_constants`).

---

### Recommendation

1. **Restrict network access**: The proof-manager remote server should only accept connections from known internal peers (gateway, consensus orchestrator). Enforce this via mutual TLS, a shared secret header, or IP allowlisting at the `RemoteComponentServer` level.
2. **Remove the skip-if-exists shortcut, or verify on retrieval**: `run_proof_verification` should not treat presence in the proof manager as proof of validity. Either re-verify on every submission, or record a "verified" flag separately from the raw proof bytes so that only gateway-verified proofs are marked as trusted.
3. **Validate proof bytes in `set_proof`**: `ProofManager::set_proof` should call `starknet_proof_verifier::verify_proof` before persisting, so the proof manager itself is a trust boundary regardless of which caller invokes it.

---

### Proof of Concept

```
# 1. Craft valid ProofFacts (all structural fields pass blockifier checks):
#    proof_version = PROOF_VERSION_V1
#    variant_marker = VIRTUAL_SNOS
#    program_hash   = any value in allowed_virtual_os_program_hashes
#    output_version = VIRTUAL_OS_OUTPUT_VERSION
#    block_number   = current_block - STORED_BLOCK_HASH_BUFFER  (old enough)
#    block_hash     = real stored hash for that block_number
#    config_hash    = virtual_os_config_hash from versioned_constants

# 2. Send SetProof to the RemoteProofManagerServer (no auth required):
POST http://<proof_manager_host>:<port>/
Body: SerdeWrapper-serialized ProofManagerRequest::SetProof(proof_facts, fake_proof_bytes)

# 3. Submit Invoke V3 transaction to the public gateway with the same proof_facts
#    and any proof bytes (they will never be verified):
starknet_add_invoke_transaction({
    type: "INVOKE", version: "0x3",
    proof_facts: <crafted above>,
    proof: <arbitrary bytes>,
    ...
})

# Result: gateway calls contains_proof → true → skips verify_proof →
#         transaction admitted with unverified ZK proof.
```

### Citations

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

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L186-235)
```rust
    #[instrument(skip_all, fields(request_id = %request_id, remote_addr = %client_peer))]
    async fn remote_component_server_handler(
        http_request: HyperRequest<Incoming>,
        request_id: RequestId,
        client_peer: SocketAddr,
        local_client: LocalComponentClient<Request, Response>,
        metrics: &'static RemoteServerMetrics,
        max_request_body_bytes: usize,
    ) -> Result<HyperResponse<Full<Bytes>>, hyper::Error> {
        trace!("Received HTTP request: {http_request:?}");
        let body_bytes =
            match Limited::new(http_request.into_body(), max_request_body_bytes).collect().await {
                Ok(collected) => collected.to_bytes(),
                Err(err) => {
                    warn!("Request body too large: {err}");
                    let server_error = ServerError::RequestBodyTooLarge(err.to_string());
                    return Ok(HyperResponse::builder()
                        .status(StatusCode::PAYLOAD_TOO_LARGE)
                        .header(CONTENT_TYPE, APPLICATION_OCTET_STREAM)
                        .body(Full::new(Bytes::from(
                            SerdeWrapper::new(server_error)
                                .wrapper_serialize()
                                .expect("Server error serialization should succeed"),
                        )))
                        .expect("Response building should succeed"));
                }
            };
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

**File:** crates/apollo_deployments/resources/services/distributed/proof_manager.json (L87-91)
```json
  "components.proof_manager.remote_server_config.#is_none": false,
  "components.proof_manager.remote_server_config.bind_ip": "0.0.0.0",
  "components.proof_manager.remote_server_config.max_streams_per_connection": 8,
  "components.proof_manager.remote_server_config.set_tcp_nodelay": true,
  "components.proof_manager.url": "remote_service",
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L426-471)
```rust
    /// Spawns a verification-only task. Used by the gateway flow, which stores the proof
    /// separately after all validations pass.
    fn spawn_proof_verification(
        &self,
        proof_facts: ProofFacts,
        proof: Proof,
    ) -> TransactionConverterResult<VerificationHandle> {
        let pmc = self.proof_manager_client.clone();
        let task_proof_facts = proof_facts.clone();
        let task_proof = proof.clone();
        let verification_task = tokio::spawn(async move {
            Self::run_proof_verification(task_proof_facts, task_proof, pmc).await?;
            Ok(())
        });
        Ok(VerificationHandle { proof_facts, proof, verification_task })
    }

    /// Spawns a single task that verifies the proof and then stores it in the proof manager.
    /// Used by the consensus flow, where tasks run concurrently with batcher execution and
    /// are awaited at fin.
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
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L291-351)
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

        if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt())
        {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Proof version {} is not allowed under this protocol version.",
                snos_proof_facts.proof_version
            )));
        }

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
        let virtual_os_config_hash = block_context.virtual_os_config_hash();
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
