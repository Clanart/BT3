### Title
`RemoteProofManagerServer.set_proof()` Lacks Access Control, Allowing Proof-Verification Bypass for Client-Side-Proving Transactions - (File: `crates/apollo_proof_manager/src/communication.rs`)

---

### Summary

`ProofManager.set_proof()` is exposed over an unauthenticated HTTP endpoint via `RemoteProofManagerServer`. Because `run_proof_verification` in `TransactionConverter` unconditionally skips cryptographic proof verification whenever `contains_proof` returns `true`, any network peer that can reach the proof-manager port can pre-populate the proof store with an arbitrary (fake) payload keyed to attacker-chosen `proof_facts`. A subsequent gateway submission carrying those same `proof_facts` will then pass all checks and be admitted to the mempool without any valid SNOS proof ever being verified.

---

### Finding Description

**Root cause — no caller check on `set_proof`**

`RemoteProofManagerServer` is a plain HTTP/2 server with no authentication, no IP allowlist, and no TLS client-certificate requirement. [1](#0-0) 

Its `handle_request` dispatches `ProofManagerRequest::SetProof` directly to `ProofManager::set_proof` without any caller identity check: [2](#0-1) 

`ProofManager::set_proof` itself only checks whether the entry already exists; it never verifies the cryptographic proof before writing: [3](#0-2) 

The `RemoteComponentServer` binds to `0.0.0.0` by default and processes every well-formed HTTP request it receives, with no authentication layer: [4](#0-3) [5](#0-4) 

**Exploitable invariant — `run_proof_verification` skips verification on cache hit**

`run_proof_verification` is the only place where `starknet_proof_verifier::verify_proof` is called. It short-circuits immediately when `contains_proof` returns `true`, without ever inspecting the stored payload: [6](#0-5) 

This is the exact analog of the external `distribute()` bug: a privileged write path (proof storage) is reachable by any caller, and a downstream consumer (the verifier) trusts the presence of the stored entry as proof of validity.

**Attack path**

1. Attacker selects valid `program_hash` (from `allowed_virtual_os_program_hashes`), a real `block_hash`/`block_number` pair that satisfies `validate_proof_block_hash`, and the correct `config_hash`. These values are all public. [7](#0-6) 

2. Attacker sends an HTTP POST to the `RemoteProofManagerServer` port with `ProofManagerRequest::SetProof(crafted_proof_facts, fake_proof_bytes)`. The server stores the entry keyed by `crafted_proof_facts.hash()`.

3. Attacker submits an Invoke V3 transaction to the gateway carrying those same `proof_facts` (and any `proof` field — it is never re-verified).

4. The gateway calls `convert_rpc_tx_to_internal_rpc_tx`, which spawns `spawn_proof_verification`: [8](#0-7) 

5. `run_proof_verification` calls `contains_proof` → `true` → returns `Ok(false)` (skipped). No cryptographic check is performed.

6. `store_proof_and_spawn_archiving` then calls `store_proof_in_proof_manager` with the same fake proof, which is a no-op because the entry already exists: [9](#0-8) 

7. The transaction passes all stateful validations (`validate_proof_facts` only checks metadata fields, not the proof bytes) and is added to the mempool. [10](#0-9) 

---

### Impact Explanation

A transaction carrying attacker-chosen `proof_facts` but no valid SNOS proof is admitted to the mempool and sequenced into a block. The client-side proving security guarantee — that every transaction with `proof_facts` has been cryptographically verified against the SNOS circuit — is completely bypassed. This matches the **High** impact scope: *"Mempool/gateway/RPC admission accepts invalid transactions … before sequencing."*

---

### Likelihood Explanation

In any distributed deployment the `RemoteProofManagerServer` port is reachable from other sequencer components over the internal network. An attacker with access to that network segment (e.g., a compromised co-located service, a misconfigured firewall, or a malicious peer in a permissionless deployment) can execute the attack with a single HTTP request before submitting the spoofed transaction. No privileged credentials are required.

---

### Recommendation

Restrict callers of `ProofManager::set_proof` (and `get_proof` / `contains_proof`) to the gateway and consensus-orchestrator components only. Concretely:

1. **Network-level**: bind `RemoteProofManagerServer` to a loopback or internal-only interface, or enforce mTLS with a per-component certificate.
2. **Application-level**: add a caller-identity token (e.g., a shared secret or a signed nonce) to `ProofManagerRequest` and validate it inside `handle_request` before dispatching `SetProof`.
3. **Defense-in-depth**: even when `contains_proof` returns `true`, re-verify the proof facts metadata (program hash, block hash, config hash) against the stored entry before skipping full cryptographic verification, so a pre-populated fake entry cannot satisfy a legitimately different set of facts.

---

### Proof of Concept

```
# 1. Craft valid proof_facts metadata (all public values):
#    proof_facts = [PROOF_VERSION, SNOS_VARIANT, allowed_program_hash,
#                   real_block_number, real_block_hash, correct_config_hash]

# 2. POST directly to the RemoteProofManagerServer (no auth required):
POST http://<proof-manager-host>:<port>/
Content-Type: application/octet-stream
Body: bincode-serialized ProofManagerRequest::SetProof(crafted_proof_facts, b"\x00")

# 3. Submit Invoke V3 tx to the gateway with the same proof_facts:
starknet_rs / curl → gateway add_transaction endpoint
  proof_facts: <crafted_proof_facts>
  proof: <any bytes>

# Result: run_proof_verification sees contains_proof == true,
#         skips verify_proof(), transaction admitted to mempool.
```

The `RemoteComponentServer` handler that processes step 2 with no authentication check: [11](#0-10)

### Citations

**File:** crates/apollo_proof_manager/src/communication.rs (L9-10)
```rust
pub type RemoteProofManagerServer =
    RemoteComponentServer<ProofManagerRequest, ProofManagerResponse>;
```

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

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L42-42)
```rust
const DEFAULT_BIND_IP: IpAddr = IpAddr::V4(Ipv4Addr::UNSPECIFIED);
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L426-441)
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
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L291-346)
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
```

**File:** crates/apollo_gateway/src/gateway.rs (L241-253)
```rust
    async fn store_proof_and_spawn_archiving(
        &self,
        proof_data: Option<(ProofFacts, Proof)>,
        tx_hash: TransactionHash,
    ) -> ProofArchiveHandle {
        let (proof_facts, proof) = proof_data?;

        // Proof is verified during conversion to internal tx. It is stored here, after
        // validation, to avoid storing proofs for rejected transactions.
        let store_result = self
            .transaction_converter
            .store_proof_in_proof_manager(proof_facts.clone(), proof.clone())
            .await;
```
