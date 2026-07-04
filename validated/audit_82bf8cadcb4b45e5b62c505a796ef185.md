### Title
`check_proof_facts` Only Validates the First Proof Fact, Allowing Unvalidated Fake Proof Facts to Be Passed to Contracts — (File: `execution/execution_constraints.cairo`)

---

### Summary

`check_proof_facts` in `execution_constraints.cairo` validates only the first proof fact in the `proof_facts` array and returns immediately, even when `proof_facts_size` indicates more data is present. An unprivileged transaction sender can append arbitrary, unvalidated proof facts beyond the first entry. Because all proof facts (including the unvalidated ones) are exposed to the called contract via `get_execution_info`, a contract that makes security-critical decisions based on proof facts beyond index 0 can be deceived, leading to direct loss of funds.

---

### Finding Description

`check_proof_facts` is called during every invoke transaction execution to validate the `proof_facts` field supplied by the transaction sender:

```cairo
// transaction_impls.cairo
local proof_facts_size;
local proof_facts: felt*;
%{ TxProofFacts %}
...
check_proof_facts(
    proof_facts_size=proof_facts_size,
    proof_facts=proof_facts,
    current_block_number=...,
    virtual_os_config_hash=...,
);
```

The implementation in `execution_constraints.cairo` is:

```cairo
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
    assert [proof_header] = ProofHeader(...);

    // Validate the virtual OS output header.
    let os_output_header = cast(&proof_facts[ProofHeader.SIZE], VirtualOsOutputHeader*);
    assert os_output_header.output_version = VIRTUAL_OS_OUTPUT_VERSION;
    assert_nn_le(os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER);
    assert_not_zero(os_output_header.base_block_hash);
    read_block_hash_from_storage(...);
    assert os_output_header.starknet_os_config_hash = virtual_os_config_hash;

    return ();   // <-- returns here; no loop, no recursion
}
```

The function:
1. Checks `proof_facts_size >= ProofHeader.SIZE + VirtualOsOutputHeader.SIZE` (a **minimum** bound, not equality).
2. Validates exactly **one** `ProofHeader` + `VirtualOsOutputHeader` pair.
3. Returns unconditionally — any bytes at offset `ProofHeader.SIZE + VirtualOsOutputHeader.SIZE` onward are **never validated**.

The use of `assert_le` (≤) rather than equality, combined with the plural naming (`proof_facts_size`, `proof_facts_start`/`proof_facts_end` in `TxInfo`), confirms the design intent is to support multiple proof facts. The function is structurally identical to the external report's bug: it processes only the first element of a sequential list and exits.

The full `proof_facts` array — including the unvalidated tail — is committed into the transaction hash and is accessible to the called contract through `get_execution_info` → `TxInfo.proof_facts_start` / `proof_facts_end`.

---

### Impact Explanation

**Direct loss of funds (Critical).**

Proof facts are the mechanism by which a contract can verify that a particular virtual-OS execution (and therefore a particular historical Starknet state) was proven. A contract that gates fund release or privileged state transitions on the content of proof facts beyond index 0 will accept attacker-supplied, OS-unvalidated data as if it were a genuine proof. The attacker can craft any `VirtualOsOutputHeader`-shaped blob at offset `ProofHeader.SIZE + VirtualOsOutputHeader.SIZE` and the OS will never check it, while the contract will observe it as a validated proof fact.

---

### Likelihood Explanation

Any invoke transaction sender can supply an arbitrarily large `proof_facts` array. The first entry is validated; everything after it is not. The attack requires only that a deployed contract reads proof facts beyond the first entry for a security decision — a realistic pattern for any contract that aggregates or chains multiple virtual-OS proofs (e.g., multi-step bridge or cross-chain state verification contracts). No privileged access, leaked key, or operator cooperation is required.

---

### Recommendation

Replace the single-pass validation with a loop that iterates over every proof fact in the array, advancing the pointer by `ProofHeader.SIZE + VirtualOsOutputHeader.SIZE` per iteration until the full `proof_facts_size` is consumed:

```cairo
func check_proof_facts{range_check_ptr, contract_state_changes: DictAccess*}(
    proof_facts_size: felt,
    proof_facts: felt*,
    current_block_number: felt,
    virtual_os_config_hash: felt,
) {
    if (proof_facts_size == 0) {
        return ();
    }

    let entry_size = ProofHeader.SIZE + VirtualOsOutputHeader.SIZE;
    assert_le(entry_size, proof_facts_size);

    // Validate the first proof fact (existing logic) ...

    // Recurse over remaining proof facts.
    return check_proof_facts(
        proof_facts_size=proof_facts_size - entry_size,
        proof_facts=&proof_facts[entry_size],
        current_block_number=current_block_number,
        virtual_os_config_hash=virtual_os_config_hash,
    );
}
```

Additionally, add an assertion that `proof_facts_size` is an exact multiple of `entry_size` to reject malformed inputs.

---

### Proof of Concept

1. Attacker deploys a target contract `C` that reads `tx_info.proof_facts_start[entry_size]` (the second proof fact) and releases funds if `os_output_header.base_block_hash` equals a chosen value `X`.
2. Attacker submits an invoke transaction with:
   - `proof_facts[0 .. entry_size)` = a **valid** proof fact (passes `check_proof_facts`).
   - `proof_facts[entry_size .. 2*entry_size)` = a **fabricated** `ProofHeader` + `VirtualOsOutputHeader` with `base_block_hash = X`.
3. The OS calls `check_proof_facts`, validates only the first entry, and returns `()`.
4. The contract `C` reads the second proof fact via `get_execution_info`, sees `base_block_hash == X`, and releases funds to the attacker.
5. The OS never detected the fabricated second entry.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L34-81)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L203-242)
```text
func fill_account_tx_info{range_check_ptr}(
    transaction_hash: felt,
    common_tx_fields: CommonTxFields*,
    account_deployment_data_size: felt,
    account_deployment_data: felt*,
    proof_facts_size: felt,
    proof_facts: felt*,
    tx_info_dst: TxInfo*,
    deprecated_tx_info_dst: DeprecatedTxInfo*,
) {
    alloc_locals;

    local signature_start: felt*;
    local signature_len: felt;
    %{ GenSignatureArg %}
    assert_nn_le(signature_len, SIERRA_ARRAY_LEN_BOUND - 1);
    assert [tx_info_dst] = TxInfo(
        version=common_tx_fields.version,
        account_contract_address=common_tx_fields.sender_address,
        max_fee=0,
        signature_start=signature_start,
        signature_end=&signature_start[signature_len],
        transaction_hash=transaction_hash,
        chain_id=common_tx_fields.chain_id,
        nonce=common_tx_fields.nonce,
        resource_bounds_start=common_tx_fields.resource_bounds,
        resource_bounds_end=&common_tx_fields.resource_bounds[common_tx_fields.n_resource_bounds],
        tip=common_tx_fields.tip,
        paymaster_data_start=common_tx_fields.paymaster_data,
        paymaster_data_end=&common_tx_fields.paymaster_data[common_tx_fields.paymaster_data_length],
        nonce_data_availability_mode=common_tx_fields.nonce_data_availability_mode,
        fee_data_availability_mode=common_tx_fields.fee_data_availability_mode,
        account_deployment_data_start=account_deployment_data,
        account_deployment_data_end=&account_deployment_data[account_deployment_data_size],
        proof_facts_start=proof_facts,
        proof_facts_end=&proof_facts[proof_facts_size],
    );
    fill_deprecated_tx_info(tx_info=tx_info_dst, dst=deprecated_tx_info_dst);
    assert_deprecated_tx_fields_consistency(tx_info=tx_info_dst);
    return ();
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
