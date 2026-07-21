### Title
`SnosProofFacts` Missing Transaction-Identity Binding Allows Cross-Transaction Proof Reuse — (`crates/starknet_api/src/transaction/fields.rs`)

---

### Summary

The `SnosProofFacts` structure and the virtual OS output format do not include the `transaction_hash` or `sender_address`. This means a cryptographically valid proof generated for transaction T1 can be attached to a structurally different transaction T2 (same base block, different calldata/sender) and will pass all proof-facts validation checks. The `ProofManager` cache further amplifies the issue: once a proof is stored for a given `proof_facts.hash()`, every subsequent transaction carrying the same proof facts skips proof verification entirely.

---

### Finding Description

**Root cause — `SnosProofFacts` carries no transaction identity.**

`SnosProofFacts` is defined as:

```rust
pub struct SnosProofFacts {
    pub proof_version: Felt,
    pub program_hash: StarkHash,
    pub block_number: BlockNumber,
    pub block_hash: BlockHash,
    pub config_hash: StarkHash,
}
``` [1](#0-0) 

The virtual OS output format that populates these fields is:

```
[output_version, base_block_number, base_block_hash, starknet_os_config_hash,
 n_l2_to_l1_messages, message_hash_0, ...]
``` [2](#0-1) 

Neither the `transaction_hash` nor the `sender_address` appears anywhere in this output. The proof therefore proves only "the virtual OS ran on block X with config Y and produced messages M" — it does **not** prove "transaction T was the one executed."

**Validation checks are blind to transaction identity.**

`validate_proof_facts` in the blockifier checks only `program_hash`, `block_hash`, `block_number`, and `config_hash`: [3](#0-2) 

The Cairo-level `check_proof_facts` performs the same four checks and nothing more: [4](#0-3) 

**`ProofManager` cache amplifies the issue.**

`run_proof_verification` short-circuits on a cache hit:

```rust
let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;
if contains_proof {
    return Ok(false);   // skip verification entirely
}
``` [5](#0-4) 

The cache key is `proof_facts.hash()` — a Poseidon hash of the proof-facts felts: [6](#0-5) 

Once any transaction deposits a proof for proof-facts F, every subsequent transaction that carries the same F (same base block, same program hash, same config hash, same L2→L1 messages) will have its proof accepted without re-verification.

**Transaction hash does include proof facts — but that is not the same as proof facts including the transaction hash.**

`get_invoke_transaction_v3_hash` chains `proof_facts_hash` into the transaction hash: [7](#0-6) 

This means changing calldata while keeping the same proof facts produces a *different* transaction hash. The proof was generated for the original transaction hash, but the proof facts do not encode that hash, so the proof verifier cannot detect the mismatch.

---

### Impact Explanation

The client-side proving design intent is that an account's `__validate__` entry point can inspect `get_execution_info().tx_info.proof_facts` and, upon finding valid proof facts, skip its own signature check (the proof is the authorisation). Because `SnosProofFacts` carries no transaction identity, an attacker who obtains a valid `(proof_facts, proof)` pair for any transaction T1 on block X can attach it to a crafted transaction T2 (different calldata, same base block) and have T2 accepted as "proven." Any account contract that gates its `__validate__` on proof-facts validity will execute T2 without a valid signature, satisfying the Critical impact criterion:

> *Invalid or unauthorized Starknet transaction accepted through account validation, signature, nonce, chain id, fee/resource bound, paymaster, or account-deployment logic.*

---

### Likelihood Explanation

- Proof facts and proofs are transmitted in the public RPC/mempool wire format (`RpcInvokeTransactionV3.proof_facts` + `.proof`), so any observer can extract them.
- Crafting T2 with copied proof facts requires no privileged access — it is a standard `starknet_addInvokeTransaction` call.
- The `ProofManager` cache means a single honest transaction permanently "unlocks" proof-free submission for all future transactions sharing the same base block and OS config.
- Any account that adopts client-side proving (the feature's stated purpose) is immediately vulnerable.

---

### Recommendation

1. **Extend the virtual OS output** to include the `transaction_hash` of the single transaction it executed. Update `VirtualOsOutputHeader` in `virtual_os_output.cairo` and `SnosProofFacts` in `fields.rs` accordingly. [8](#0-7) 

2. **Add a binding check** in both `validate_proof_facts` (Rust) and `check_proof_facts` (Cairo) that asserts `proof_facts.transaction_hash == current_tx_hash`. [3](#0-2) 

3. **Scope the `ProofManager` cache key** to `(proof_facts_hash, transaction_hash)` rather than `proof_facts_hash` alone, so a cached proof cannot be reused across transactions. [9](#0-8) 

---

### Proof of Concept

```
Setup:
  Block X is finalized. Alice holds an account that skips __validate__ when
  proof_facts are non-empty and structurally valid.

Step 1 — Alice proves her own transaction:
  Alice submits T1 = Invoke(sender=Alice, calldata=transfer(Bob,100),
                            proof_facts=F, proof=P)
  where F = [PROOF_VERSION, VIRTUAL_SNOS, prog_hash,
             VIRTUAL_OS_OUTPUT_VERSION, X, hash(X), cfg_hash, 0]
  Gateway calls verify_proof(F, P) → OK; stores P in ProofManager[hash(F)].

Step 2 — Mallory copies proof facts:
  Mallory crafts T2 = Invoke(sender=Alice, calldata=transfer(Mallory,100),
                              proof_facts=F, proof=P)
  T2 has a different tx_hash than T1 (different calldata), but identical F.

Step 3 — Gateway processes T2:
  contains_proof(F) → true  →  proof verification SKIPPED.
  validate_proof_facts(F) checks program_hash / block_hash / config_hash → all pass.
  No check that F.transaction_hash == T2.tx_hash (field does not exist).

Step 4 — Execution:
  check_proof_facts passes. Alice's __validate__ sees non-empty valid proof_facts
  and skips signature verification. T2 executes: 100 STRK transferred to Mallory.
```

### Citations

**File:** crates/starknet_api/src/transaction/fields.rs (L643-645)
```rust
    pub fn hash(&self) -> Felt {
        HashChain::new().chain_iter(self.0.iter()).get_poseidon_hash()
    }
```

**File:** crates/starknet_api/src/transaction/fields.rs (L727-736)
```rust
/// Contains the required fields for valid SNOS proof facts.
///
/// A valid SNOS proof facts structure must include these fields as its first five entries.
pub struct SnosProofFacts {
    pub proof_version: Felt,
    pub program_hash: StarkHash,
    pub block_number: BlockNumber,
    pub block_hash: BlockHash,
    pub config_hash: StarkHash,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/virtual_os_output.cairo (L14-24)
```text
// 1. Output format:
//    The output is a flat array of felts with the following layout:
//      [output_version, base_block_number, base_block_hash, starknet_os_config_hash,
//       n_l2_to_l1_messages, message_hash_0, message_hash_1, ...]
//    - output_version: the VIRTUAL_OS_OUTPUT_VERSION constant.
//    - base_block_number / base_block_hash: the block this run is based on. The hash is
//      computed (proven) by the OS from the block info and the initial state root.
//    - starknet_os_config_hash: Poseidon hash of the Starknet OS config.
//    - n_l2_to_l1_messages: count of L2-to-L1 message hashes that follow.
//    - Each message hash is Poseidon([from_address, to_address, payload_size, ...payload]).
//    No state diff, data availability, or state roots are included.
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/virtual_os_output.cairo (L59-67)
```text
// The header of the virtual OS output.
struct VirtualOsOutputHeader {
    output_version: felt,
    // The block number and hash that this run is based on.
    base_block_number: felt,
    base_block_hash: felt,
    starknet_os_config_hash: felt,
    n_l2_to_l1_messages: felt,
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L34-82)
```text
func check_proof_facts{range_check_ptr, contract_state_changes: DictAccess*}(
    proof_facts_size: felt,
    proof_facts: felt*,
    current_block_number: felt,
    virtual_os_config_hash: felt,
) {
    if (proof_facts_size == 0) {
        return ();
    }

    assert_le(ProofHeader.SIZE + VirtualOsOutputHeader.SIZE, proof_facts_size);

    // Validate the proof header.
    let proof_header = cast(proof_facts, ProofHeader*);
    assert is_program_hash_allowed(proof_header.program_hash) = TRUE;
    // Proof version and variant are for future compatibility.
    assert [proof_header] = ProofHeader(
        proof_version=PROOF_VERSION,
        proof_variant=VIRTUAL_SNOS,
        program_hash=proof_header.program_hash,
    );

    // Validate the virtual OS output header.
    let os_output_header = cast(&proof_facts[ProofHeader.SIZE], VirtualOsOutputHeader*);

    with_attr error_message("Virtual OS output version is not supported") {
        assert os_output_header.output_version = VIRTUAL_OS_OUTPUT_VERSION;
    }

    // Validate that the proof facts block number is not too recent.
    // (This is a sanity check - the following non-zero check ensures that the block hash is
    // not trivial).
    assert_nn_le(
        os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
    );
    // Not all block hashes are stored in the contract; Make sure the requested one is not trivial.
    assert_not_zero(os_output_header.base_block_hash);

    // validate that the proof facts block hash is the true hash of the proof facts block number.
    read_block_hash_from_storage(
        block_number=os_output_header.base_block_number,
        expected_block_hash=os_output_header.base_block_hash,
    );

    // validate that the proof facts config hash is the true hash of the OS config.
    assert os_output_header.starknet_os_config_hash = virtual_os_config_hash;

    return ();
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

**File:** crates/starknet_api/src/transaction_hash.rs (L399-404)
```rust
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
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
