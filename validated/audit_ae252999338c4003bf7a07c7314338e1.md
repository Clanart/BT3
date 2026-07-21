### Title
`FsProofStorage` Reads Proof Bytes from Flat Files Without Integrity Verification, Allowing a Local Attacker to Substitute an Arbitrary Proof That Bypasses Re-verification - (File: `crates/apollo_proof_manager/src/proof_storage.rs`)

### Summary

`FsProofStorage` persists cryptographic proof blobs as raw bytes in unprotected flat files keyed only by a directory name derived from `facts_hash`. When a proof is later retrieved via `get_proof`, the bytes are read back with no integrity check against the `facts_hash`. Separately, `run_proof_verification` in `TransactionConverter` unconditionally skips proof verification whenever `contains_proof` returns `true`. A local attacker who can write to the proof storage directory can therefore replace a stored proof file with arbitrary bytes; the sequencer will serve those bytes as a verified proof, and any subsequent transaction carrying the same `proof_facts` will also bypass verification entirely.

### Finding Description

`FsProofStorage::read_proof_from_file` reads the stored file and wraps the raw bytes in a `Proof` struct with no hash or MAC check:

```rust
// crates/apollo_proof_manager/src/proof_storage.rs  lines 108-113
async fn read_proof_from_file(&self, facts_hash: Felt) -> FsProofStorageResult<Proof> {
    let file_path = self.get_persistent_dir(facts_hash).join("proof");
    let buffer = tokio::fs::read(&file_path).await?;
    Ok(Proof::from(buffer))   // ← raw bytes, no re-hash against facts_hash
}
``` [1](#0-0) 

`ProofManager::get_proof` falls through to this call without any post-read verification:

```rust
// crates/apollo_proof_manager/src/proof_manager.rs  lines 68-83
pub async fn get_proof(&self, proof_facts: ProofFacts) -> ... {
    let facts_hash = proof_facts.hash();
    if let Some(proof) = self.cache.get(&facts_hash) { return Ok(Some(proof)); }
    let proof = self.proof_storage.get_proof(facts_hash).await?;  // ← no integrity check
    ...
}
``` [2](#0-1) 

`run_proof_verification` in `TransactionConverter` skips the cryptographic verifier whenever the proof is already "known":

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs  lines 403-407
let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;
if contains_proof {
    return Ok(false);   // ← verification skipped entirely
}
``` [3](#0-2) 

`contains_proof` in `FsProofStorage` only checks whether the directory exists on disk — it does not verify the file contents:

```rust
// crates/apollo_proof_manager/src/proof_storage.rs  lines 163-165
async fn contains_proof(&self, facts_hash: Felt) -> Result<bool, Self::Error> {
    Ok(tokio::fs::try_exists(self.get_persistent_dir(facts_hash)).await?)
}
``` [4](#0-3) 

The tampered proof bytes are then embedded into the reconstructed RPC transaction served to callers:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs  lines 212-216
let proof = if tx.proof_facts.is_empty() {
    Proof::default()
} else {
    self.get_proof(&tx.proof_facts).await?   // ← returns tampered bytes
};
``` [5](#0-4) 

The proof bytes are not part of the internal transaction representation and do not affect the transaction hash, so no hash-based check catches the substitution. [6](#0-5) 

### Impact Explanation

A local attacker who overwrites `<persistent_root>/<aa>/<bb>/<facts_hash>/proof` with arbitrary bytes causes two distinct effects:

1. **Wrong authoritative value served via RPC / consensus reconstruction.** `convert_internal_rpc_tx_to_rpc_tx` calls `get_proof` and embeds the tampered bytes into the `RpcInvokeTransactionV3.proof` field. Any RPC caller (block explorer, L1 bridge, downstream verifier) receives a transaction whose proof field does not correspond to the claimed `proof_facts`. This matches the impact: *"High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

2. **Verification bypass for all future transactions with the same `proof_facts`.** Because `contains_proof` returns `true` (the directory still exists), `run_proof_verification` skips `starknet_proof_verifier::verify_proof` for every subsequent transaction carrying the same `proof_facts`. An attacker can therefore submit a second transaction with a completely invalid proof blob and have it accepted without cryptographic verification. This matches: *"High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."* [7](#0-6) 

### Likelihood Explanation

The proof storage root is a plain filesystem directory configured via `ProofManagerConfig.persistent_root`. [8](#0-7)  Any process running as the same OS user as the sequencer — or any process with write access to that directory — can perform the substitution without any special privilege. The directory structure is fully predictable from the `facts_hash` hex encoding. [9](#0-8)  No existing code path re-hashes or re-verifies the proof bytes after they are written.

### Recommendation

- **Short term:** After reading a proof from disk, recompute `verify_proof(proof_facts, proof)` and reject the result if verification fails. This closes both the tampered-read and the verification-bypass paths.
- **Long term:** Store a keyed HMAC or a Poseidon commitment of the proof bytes alongside the file (or inside the directory), and verify it on every read. Alternatively, store proofs inside the MDBX database that already provides transactional integrity, rather than in a bare filesystem directory.

### Proof of Concept

```
# 1. Sequencer receives a valid invoke-v3 tx with proof_facts P and valid proof Q.
#    verify_proof(P, Q) passes; directory <root>/aa/bb/<hash(P)>/proof is written.

# 2. Local attacker overwrites the proof file:
echo -n "AAAAAAAAAA" > <persistent_root>/aa/bb/<hash(P)>/proof

# 3. Sequencer receives a second invoke-v3 tx with the same proof_facts P
#    but a completely invalid proof Q''.
#    run_proof_verification: contains_proof(P) → true (directory exists) → skip verify.
#    Transaction accepted; Q'' is never checked against P.

# 4. Any RPC caller that fetches the first transaction gets proof bytes "AAAAAAAAAA"
#    embedded in RpcInvokeTransactionV3.proof — an authoritative-looking wrong value.
``` [10](#0-9) [11](#0-10)

### Citations

**File:** crates/apollo_proof_manager/src/proof_storage.rs (L48-53)
```rust
    fn get_proof_dir(&self, facts_hash: Felt) -> PathBuf {
        let facts_hash = hex::encode(facts_hash.to_bytes_be());
        let (first_msb_byte, second_msb_byte, _rest_of_bytes) =
            (&facts_hash[..2], &facts_hash[2..4], &facts_hash[4..]);
        PathBuf::from(first_msb_byte).join(second_msb_byte).join(facts_hash)
    }
```

**File:** crates/apollo_proof_manager/src/proof_storage.rs (L94-113)
```rust
    /// Writes a proof to a file in binary format.
    /// The file is named `proof` inside the given directory.
    async fn write_proof_to_file(&self, path: &Path, proof: &Proof) -> FsProofStorageResult<()> {
        let path = path.join("proof");
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }

        let mut file = tokio::fs::File::create(&path).await?;
        file.write_all(&proof.0).await?;
        file.flush().await?;
        Ok(())
    }

    /// Reads a proof from a file in binary format.
    async fn read_proof_from_file(&self, facts_hash: Felt) -> FsProofStorageResult<Proof> {
        let file_path = self.get_persistent_dir(facts_hash).join("proof");
        let buffer = tokio::fs::read(&file_path).await?;
        Ok(Proof::from(buffer))
    }
```

**File:** crates/apollo_proof_manager/src/proof_storage.rs (L163-165)
```rust
    async fn contains_proof(&self, facts_hash: Felt) -> Result<bool, Self::Error> {
        Ok(tokio::fs::try_exists(self.get_persistent_dir(facts_hash)).await?)
    }
```

**File:** crates/apollo_proof_manager/src/proof_manager.rs (L48-51)
```rust
    pub fn new(config: ProofManagerConfig) -> Self {
        let proof_storage =
            FsProofStorage::new(config.persistent_root).expect("Failed to create proof storage.");
        Self { proof_storage, cache: ProofCache::new(config.cache_size) }
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

**File:** crates/apollo_proof_manager/src/proof_manager.rs (L68-83)
```rust
    pub async fn get_proof(
        &self,
        proof_facts: ProofFacts,
    ) -> Result<Option<Proof>, FsProofStorageError> {
        let facts_hash = proof_facts.hash();
        // Check cache first.
        if let Some(proof) = self.cache.get(&facts_hash) {
            return Ok(Some(proof));
        }
        // Fallback to filesystem.
        let proof = self.proof_storage.get_proof(facts_hash).await?;
        if let Some(proof) = &proof {
            self.cache.insert(facts_hash, proof.clone());
        }
        Ok(proof)
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L212-216)
```rust
                let proof = if tx.proof_facts.is_empty() {
                    Proof::default()
                } else {
                    self.get_proof(&tx.proof_facts).await?
                };
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L395-424)
```rust
    /// Runs proof verification: checks if the proof already exists, and if not, verifies it.
    /// Returns `true` if verification was performed, `false` if skipped (proof already stored).
    /// This is the shared verification logic used by both gateway and consensus flows.
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
