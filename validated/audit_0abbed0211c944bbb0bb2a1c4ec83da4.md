### Title
Unvalidated `proof_facts_size` in Invoke Transactions Causes OS Assertion Failure and Network Halt — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

---

### Summary

An unprivileged transaction sender can submit a signed invoke transaction with a non-zero but structurally undersized `proof_facts_size` (e.g., `1`). When the StarkNet OS Cairo program processes the block containing this transaction, it executes an unguarded `assert_le` that fails hard — outside any per-transaction revert scope — causing the entire OS execution to abort. Because the OS program cannot complete, no valid STARK proof can be generated for that block, permanently halting block finalization.

---

### Finding Description

**Root cause — `execution_constraints.cairo`, line 44:**

```cairo
assert_le(ProofHeader.SIZE + VirtualOsOutputHeader.SIZE, proof_facts_size);
```

This assertion is reached whenever `proof_facts_size != 0`. It enforces that the supplied array is large enough to hold a full `ProofHeader` followed by a `VirtualOsOutputHeader`. If an attacker supplies any value `1 ≤ proof_facts_size < ProofHeader.SIZE + VirtualOsOutputHeader.SIZE`, the assertion fails with a hard Cairo VM error — not a graceful revert.

**Call chain:**

`execute_invoke_function_transaction` (`transaction_impls.cairo`, line 313) calls `check_proof_facts` **before** `%{ StartTx %}` and outside any revert-log scope:

```cairo
// transaction_impls.cairo lines 313-318
check_proof_facts(
    proof_facts_size=proof_facts_size,
    proof_facts=proof_facts,
    current_block_number=block_context.block_info_for_execute.block_number,
    virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
);
```

`proof_facts_size` and `proof_facts` are loaded directly from the transaction via the `%{ TxProofFacts %}` hint. They are attacker-controlled fields included in the transaction hash:

```cairo
// transaction_hash.cairo lines 209-213
if (proof_facts_size != 0) {
    poseidon_hash_update_with_nested_hash(
        data_ptr=proof_facts, data_length=proof_facts_size
    );
}
```

Because `proof_facts` participates in the transaction hash, an attacker can sign a transaction with `proof_facts_size = 1` and have it accepted by the account's `__validate__` entry point (which only verifies the hash, not the semantic validity of proof_facts). The blockifier (Rust execution engine) executes `__validate__` and `__execute__` entry points; it does not run `check_proof_facts`, which lives exclusively in the OS Cairo program. The transaction therefore passes blockifier-level validation and is included in a block. When the prover subsequently runs the OS Cairo program over that block, the assertion at line 44 fires, aborting the OS run.

---

### Impact Explanation

A failed Cairo assertion in the OS program is not a per-transaction revert; it is a total VM abort. The prover cannot produce a valid STARK proof for any block that contains the malicious transaction. Without a valid proof, the block cannot be finalized on L1. The sequencer must either:

- Identify and surgically remove the offending transaction (non-trivial, requires out-of-band detection), or
- Halt block production entirely.

Either outcome prevents the network from confirming new transactions, matching the **High — Network not being able to confirm new transactions (total network shutdown)** impact class.

---

### Likelihood Explanation

- `proof_facts` is an attacker-controlled field in the invoke transaction format, included in the transaction hash and therefore signable by any account.
- The blockifier does not execute `check_proof_facts`; the only enforcement is inside the OS Cairo program.
- Setting `proof_facts_size = 1` requires no special privilege, no leaked key, and no operator cooperation.
- A single such transaction per block is sufficient to abort the OS run for that block.

---

### Recommendation

1. **Blockifier-level pre-validation:** Before accepting an invoke transaction into the mempool or a block, validate that `proof_facts_size` is either `0` or `≥ ProofHeader.SIZE + VirtualOsOutputHeader.SIZE`. Reject transactions that violate this invariant with a user-visible error, preventing them from ever reaching the OS.

2. **OS-level defensive guard:** Replace the bare `assert_le` with a conditional that returns a per-transaction failure (revert) rather than a hard OS abort when the size is invalid, so a single malformed transaction cannot poison an entire block.

---

### Proof of Concept

1. Construct a valid v3 invoke transaction with `proof_facts = [0xdeadbeef]` (one felt) and `proof_facts_size = 1`.
2. Compute the transaction hash including the proof_facts commitment (as per `compute_invoke_transaction_hash`, lines 209–213 of `transaction_hash.cairo`).
3. Sign the transaction with a funded account's private key.
4. Submit to the sequencer. The blockifier validates signature and nonce — both pass. The transaction is included in the next block.
5. The prover runs the OS Cairo program over the block. Execution reaches `check_proof_facts` → `assert_le(ProofHeader.SIZE + VirtualOsOutputHeader.SIZE, 1)` → assertion fails → OS aborts.
6. No valid proof is generated. The block cannot be finalized. Repeat for each new block the sequencer attempts to build. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L40-44)
```text
    if (proof_facts_size == 0) {
        return ();
    }

    assert_le(ProofHeader.SIZE + VirtualOsOutputHeader.SIZE, proof_facts_size);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L279-318)
```text
    local proof_facts_size;
    local proof_facts: felt*;
    %{ TxProofFacts %}

    let poseidon_ptr = builtin_ptrs.selectable.poseidon;
    with poseidon_ptr {
        let transaction_hash = compute_invoke_transaction_hash(
            common_fields=common_tx_fields,
            execution_context=tx_execution_context,
            account_deployment_data_size=account_deployment_data_size,
            account_deployment_data=account_deployment_data,
            proof_facts_size=proof_facts_size,
            proof_facts=proof_facts,
        );
    }
    update_poseidon_in_builtin_ptrs(poseidon_ptr=poseidon_ptr);

    %{ AssertTransactionHash %}

    // Write the transaction info and complete the ExecutionInfo struct.
    tempvar tx_info = tx_execution_info.tx_info;
    fill_account_tx_info(
        transaction_hash=transaction_hash,
        common_tx_fields=common_tx_fields,
        account_deployment_data_size=account_deployment_data_size,
        account_deployment_data=account_deployment_data,
        proof_facts_size=proof_facts_size,
        proof_facts=proof_facts,
        tx_info_dst=tx_info,
        deprecated_tx_info_dst=tx_execution_context.deprecated_tx_info,
    );

    check_and_increment_nonce(tx_info=tx_info);

    check_proof_facts(
        proof_facts_size=proof_facts_size,
        proof_facts=proof_facts,
        current_block_number=block_context.block_info_for_execute.block_number,
        virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L208-216)
```text
        // For backward compatibility, we don't hash proof facts if they are empty.
        if (proof_facts_size != 0) {
            poseidon_hash_update_with_nested_hash(
                data_ptr=proof_facts, data_length=proof_facts_size
            );
        }
    }
    let transaction_hash = poseidon_hash_finalize(hash_state=hash_state);
    return transaction_hash;
```
