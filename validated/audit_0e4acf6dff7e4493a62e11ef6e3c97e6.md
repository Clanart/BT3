### Title
Unauthenticated `SetProof` on `RemoteProofManagerServer` Allows Injection of Fake Proofs, Bypassing Proof Verification in Gateway and Consensus Admission - (`crates/apollo_proof_manager/src/communication.rs`)

---

### Summary

The `RemoteProofManagerServer` exposes a `SetProof` endpoint on `0.0.0.0` with no authentication. Any network peer that can reach the proof manager's port can inject arbitrary `(ProofFacts, Proof)` pairs directly into the proof store. Because `run_proof_verification` in `TransactionConverter` unconditionally skips cryptographic verification when `contains_proof` returns `true`, a pre-injected fake proof causes the gateway and consensus flow to accept transactions carrying invalid proofs without ever verifying them.

---

### Finding Description

**Root cause — unauthenticated write endpoint:**

`RemoteProofManagerServer` is a plain gRPC-style server with no authentication layer. Its `handle_request` dispatches `SetProof` directly to `ProofManager::set_proof` without any caller identity check:

```rust
// crates/apollo_proof_manager/src/communication.rs
ProofManagerRequest::SetProof(proof_facts, proof) => ProofManagerResponse::SetProof(
    self.set_proof(proof_facts, proof).await ...
),
```

`ProofManager::set_proof` itself performs no cryptographic verification — it only checks whether the entry already exists, then writes unconditionally:

```rust
// crates/apollo_proof_manager/src/proof_manager.rs:54-65
pub async fn set_proof(&self, proof_facts: ProofFacts, proof: Proof) -> ... {
    if self.contains_proof(proof_facts.clone()).await? {
        return Ok(());
    }
    let facts_hash = proof_facts.hash();
    self.proof_storage.set_proof(facts_hash, proof.clone()).await?;
    self.cache.insert(facts_hash, proof);
    Ok(())
}
```

The deployment config confirms the server binds to all interfaces:

```json
// crates/apollo_deployments/resources/services/distributed/proof_manager.json:88
"components.proof_manager.remote_server_config.bind_ip": "0.0.0.0"
```

**Verification bypass — the `contains_proof` short-circuit:**

`run_proof_verification` is the single function that guards both the gateway and consensus flows. It skips cryptographic verification entirely when the proof is already present in the store:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs:398-407
async fn run_proof_verification(
    proof_facts: ProofFacts,
    proof: Proof,
    proof_manager_client: SharedProofManagerClient,
) -> Result<bool, TransactionConverterError> {
    let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;
    if contains_proof {
        return Ok(false);   // ← verification skipped, no cryptographic check
    }
    // starknet_proof_verifier::verify_proof(...) only reached if NOT already stored
    ...
}
```

**Gateway flow:**

After `run_proof_verification` returns `Ok(false)` (skipped), `store_proof_and_spawn_archiving` calls `store_proof_in_proof_manager`, which calls `set_proof` again. Because `contains_proof` is already `true`, the real proof from the user is silently discarded and the fake proof remains:

```rust
// crates/apollo_gateway/src/gateway.rs:248-253
// "Proof is verified during conversion to internal tx."
let store_result = self.transaction_converter
    .store_proof_in_proof_manager(proof_facts.clone(), proof.clone())
    .await;
```

**Consensus flow:**

`spawn_verify_and_store_proof` also calls `run_proof_verification`. When it returns `Ok(false)` (skipped), the `!verified` branch exits without error, so the proposal continues:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs:454-459
let verified = Self::run_proof_verification(...).await?;
if !verified {
    return Ok(());   // ← silently accepts, fake proof already in store
}
```

**Reproposal / RPC round-trip:**

When `convert_internal_rpc_tx_to_rpc_tx` reconstructs the `RpcTransaction` for broadcast or RPC response, it fetches the proof from the proof manager:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs:212-216
let proof = if tx.proof_facts.is_empty() {
    Proof::default()
} else {
    self.get_proof(&tx.proof_facts).await?   // ← returns the fake proof
};
```

The fake proof is then embedded in the `RpcTransaction` returned to callers and broadcast to consensus peers.

---

### Impact Explanation

An attacker who can reach the proof manager's remote port (any pod in the same Kubernetes cluster by default, or any host if the port is externally reachable) can:

1. **Pre-inject a fake proof** for a known `proof_facts` value (e.g., derived from a public block hash and block number) via a direct `SetProof` RPC call.
2. **Submit a transaction** with those `proof_facts` and any `proof` payload (or the same fake one). The gateway calls `run_proof_verification` → `contains_proof` returns `true` → cryptographic verification is skipped → the transaction is admitted to the mempool.
3. **The transaction is included in a block.** The consensus flow also skips verification for the same reason.
4. **`convert_internal_rpc_tx_to_rpc_tx`** returns the fake proof as part of the authoritative `RpcTransaction` response, and the fake proof is written to the GCS proof archive.

This matches:
- **High. Mempool/gateway/RPC admission accepts invalid transactions** — a transaction whose proof was never cryptographically verified is admitted.
- **High. RPC execution returns an authoritative-looking wrong value** — `convert_internal_rpc_tx_to_rpc_tx` returns a transaction carrying a fake proof as if it were valid.

---

### Likelihood Explanation

The `RemoteProofManagerServer` is deployed in production with `bind_ip: "0.0.0.0"` and no authentication. In a standard Kubernetes cluster without explicit `NetworkPolicy` restrictions, any pod can reach any other pod's port. The `ProofFacts` structure (containing `proof_version`, `program_hash`, `block_number`, `block_hash`, `config_hash`) is derivable from public chain data. The attack requires only a single unauthenticated gRPC call before the target transaction is processed.

---

### Recommendation

1. **Add authentication to `RemoteProofManagerServer`**: Require mutual TLS or a shared secret token for all inbound connections. The `RemoteServerConfig` struct should include TLS configuration.

2. **Never skip cryptographic verification based on `contains_proof`**: `run_proof_verification` must not treat a pre-existing store entry as proof of validity. The correct pattern is: verify first, then store. If deduplication is desired, verify the proof regardless and only skip the *storage* step if already present.

3. **Restrict network exposure**: Apply Kubernetes `NetworkPolicy` to limit which pods can reach the proof manager's port.

---

### Proof of Concept

```
# Step 1: Attacker injects a fake proof for known proof_facts
# (proof_facts derived from public block data: block_number=N, block_hash=H)
grpc_call RemoteProofManagerServer:SetProof(
    proof_facts = [PROOF_VERSION, VIRTUAL_SNOS, PROGRAM_HASH, OS_OUTPUT_VERSION, N, H, CONFIG_HASH],
    proof       = <arbitrary bytes>
)
# → ProofManager::set_proof stores fake proof, no verification performed

# Step 2: User submits invoke transaction with the same proof_facts
POST /gateway  { type: INVOKE, proof_facts: [...], proof: <any> }
# → run_proof_verification: contains_proof=true → returns Ok(false) → skips verify_proof()
# → transaction admitted to mempool

# Step 3: Transaction included in block
# → spawn_verify_and_store_proof: contains_proof=true → !verified → Ok(()) → no rejection

# Step 4: RPC round-trip returns fake proof
# → convert_internal_rpc_tx_to_rpc_tx: get_proof(proof_facts) → fake proof
# → RpcTransaction broadcast to peers and returned to RPC callers contains fake proof
```

**Affected files:**
- [1](#0-0) 
- [2](#0-1) 
- [3](#0-2) 
- [4](#0-3) 
- [5](#0-4) 
- [6](#0-5)

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

**File:** crates/apollo_proof_manager/src/proof_manager.rs (L54-65)
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
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L212-216)
```rust
                let proof = if tx.proof_facts.is_empty() {
                    Proof::default()
                } else {
                    self.get_proof(&tx.proof_facts).await?
                };
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L453-459)
```rust
        tokio::spawn(async move {
            let verified =
                Self::run_proof_verification(proof_facts.clone(), proof.clone(), pmc.clone())
                    .await?;

            if !verified {
                return Ok(());
```

**File:** crates/apollo_deployments/resources/services/distributed/proof_manager.json (L87-89)
```json
  "components.proof_manager.remote_server_config.#is_none": false,
  "components.proof_manager.remote_server_config.bind_ip": "0.0.0.0",
  "components.proof_manager.remote_server_config.max_streams_per_connection": 8,
```
