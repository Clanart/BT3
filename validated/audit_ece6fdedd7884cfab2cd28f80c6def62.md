### Title
Bootstrap Sender Address Bypasses Signature Validation for Class Declaration — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special "bootstrap" path that completely skips nonce checking, signature validation (`__validate_declare__`), and fee charging. The guard condition uses only a plain felt comparison (`sender_address == 'BOOTSTRAP'`), which any unprivileged user can satisfy by crafting a declare transaction whose sender address equals the felt encoding of the ASCII string `"BOOTSTRAP"`. This is the direct analog of the external report's pattern: a privileged-looking sentinel value (`address(0)` there, `'BOOTSTRAP'` here) that is reachable by anyone and bypasses all authentication.

---

### Finding Description

In `execute_declare_transaction`, after computing the transaction hash and filling `tx_info`, the OS checks:

```cairo
if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
    let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
    if (max_possible_fee == 0) {
        assert_not_zero(compiled_class_hash);
        dict_update{dict_ptr=contract_class_changes}(
            key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
        );
        %{ SkipTx %}
        return ();
    }
}
``` [1](#0-0) 

When this branch is taken, the function returns immediately after writing to `contract_class_changes`, skipping:

- `check_and_increment_nonce` (line 779) — no nonce replay protection
- `non_reverting_select_execute_entry_point_func` for `__validate_declare__` (line 804) — no signature check
- `charge_fee` (line 822) — no fee payment [2](#0-1) 

The four conditions an attacker must satisfy are:

| Condition | Attacker control |
|---|---|
| `sender_address == 'BOOTSTRAP'` | Freely chosen felt value in the transaction |
| `tx_info.nonce == 0` | Set nonce field to 0 |
| `tx_info.version == 3` | Set version field to 3 |
| `max_possible_fee == 0` | Set all resource-bound `max_amount` and `max_price_per_unit` to 0 |

`'BOOTSTRAP'` is a Cairo short-string literal — a plain `felt` with no special on-chain meaning. There is no deployed account contract at that address, no key, and no privilege check. Any party who can submit a transaction to the sequencer can set `sender_address` to this value.

The only constraint on the declared class is that `finalize_class_hash` must verify the Sierra class hash pre-image (line 738–743), and `compiled_class_hash` must be non-zero (line 769). The OS does **not** verify that `compiled_class_hash` corresponds to the Sierra class — it is a freely chosen felt. [3](#0-2) [4](#0-3) 

The `dict_update` call uses `prev_value=0`, which enforces that a class hash can only be registered once. This means an attacker can front-run any legitimate class declaration and permanently associate a valid Sierra class hash with an attacker-chosen `compiled_class_hash`.

---

### Impact Explanation

**Critical — Direct loss of funds.**

The `contract_class_changes` dictionary maps Sierra class hashes to compiled (CASM) class hashes. When a contract is executed, the OS resolves the class hash to the compiled class hash and runs the corresponding bytecode. If an attacker registers a valid Sierra class hash with a malicious `compiled_class_hash` (one whose bytecode drains funds, ignores access control, or transfers ownership), every contract subsequently deployed under that class hash will execute the attacker's code.

Concretely:
1. Attacker observes a pending legitimate declare transaction for a widely-used account class (e.g., a new OpenZeppelin account version).
2. Attacker front-runs it with a bootstrap declare using the same Sierra class hash but a `compiled_class_hash` pointing to malicious CASM.
3. The legitimate declare fails (`prev_value=0` is no longer satisfied).
4. All users who deploy accounts with that class hash deploy wallets whose `__execute__` runs the attacker's bytecode, enabling direct theft of all funds held by those accounts.

Additionally, corrupting the compiled class hash of a system-critical class (fee token, block hash contract) could prevent the network from processing any further transactions — a total network shutdown.

---

### Likelihood Explanation

**Medium.**

The four conditions are trivially satisfiable by any user who can submit a transaction to the sequencer. No private key, no deployed account, and no privileged role is required. The only practical barrier is that the attacker must act before the legitimate declarer in the same or an earlier block (front-running). On a public network this is straightforward. The bootstrap path appears intended for operator-only use during system initialization, but the OS enforces no such restriction.

---

### Recommendation

1. **Remove the bootstrap path entirely** once the system is initialized, or gate it behind a block-number check (e.g., only valid at block 0).
2. If the bootstrap path must remain, require the `sender_address` to be a specific, pre-committed privileged address (e.g., a sequencer-controlled address stored in `block_context`), not a freely chosen felt literal.
3. At minimum, add `assert_not_zero(sender_address - 'BOOTSTRAP')` in the normal declare flow and document that `'BOOTSTRAP'` is a reserved sentinel — but this alone does not fix the root cause.
4. Verify that `compiled_class_hash` corresponds to the provided Sierra class by cross-referencing the `compiled_class_facts` loaded at the start of the OS run, as is done in `validate_compiled_class_facts`. [5](#0-4) 

---

### Proof of Concept

**Attacker steps (no account contract, no private key required):**

1. Observe a pending declare transaction for Sierra class hash `C` with legitimate `compiled_class_hash` `H_legit`.
2. Craft a declare transaction with:
   - `sender_address = 0x424f4f545354524150` (felt encoding of `"BOOTSTRAP"`)
   - `nonce = 0`
   - `version = 3`
   - All resource bounds set to 0 (so `max_possible_fee == 0`)
   - `class_hash = C` (same Sierra class hash, valid pre-image supplied via `SetComponentHashes` hint)
   - `compiled_class_hash = H_evil` (hash of attacker-controlled malicious CASM)
3. Submit to the sequencer before the legitimate transaction is included.
4. The OS executes the bootstrap branch at line 764–775, writes `contract_class_changes[C] = H_evil`, and returns without any signature or nonce check.
5. The legitimate declare at step 1 now fails because `prev_value=0` is violated.
6. Any user who deploys a contract with class hash `C` deploys a wallet running `H_evil` bytecode. The attacker's `__execute__` implementation transfers all funds to the attacker. [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L734-743)
```text
        // Ensure the given class hash is a result of a Sierra class hash calculation.
        local contract_class_component_hashes: ContractClassComponentHashes*;
        %{ SetComponentHashes %}

        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L761-776)
```text
    // Do not run validate or perform any account-related actions for declare transactions that
    // meet the following conditions.
    // This flow is used for the sequencer to bootstrap a new system.
    if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
        let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
        if (max_possible_fee == 0) {
            // Declare the class hash and skip the rest of the transaction.
            // Note that prev_value=0 enforces that a class may be declared only once.
            assert_not_zero(compiled_class_hash);
            dict_update{dict_ptr=contract_class_changes}(
                key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
            );
            %{ SkipTx %}
            return ();
        }
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L778-825)
```text
    // Increment nonce.
    check_and_increment_nonce(tx_info=tx_info);

    // Prepare the validate execution context.
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(key=sender_address);
    // The calldata for declare tx is the class hash.
    local validate_declare_execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=state_entry.class_hash,
        calldata_size=1,
        calldata=class_hash_ptr,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_validate,
            tx_info=tx_info,
            caller_address=ORIGIN_ADDRESS,
            contract_address=sender_address,
            selector=VALIDATE_DECLARE_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=deprecated_tx_info,
    );

    let remaining_gas = get_initial_user_gas_bound(common_tx_fields=common_tx_fields);
    with remaining_gas {
        cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
        // Run the account contract's "__validate_declare__" entry point.
        %{ StartTx %}
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_declare_execution_context
        );
    }
    // TODO(Yoni): calculate the gas consumed and use it to charge fee (for all transactions).
    if (is_deprecated == 0) {
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }

    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );

    // Charge fee.
    charge_fee(
        block_context=block_context, tx_execution_context=validate_declare_execution_context
    );
    %{ EndTx %}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L97-138)
```text
// Validates the compiled class facts structure and hash, using the hint variable
// `bytecode_segment_structures` - a mapping from compilied class hash to the structure.
func validate_compiled_class_facts{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    n_compiled_class_facts, compiled_class_facts: CompiledClassFact*, builtin_costs: felt*
) {
    if (n_compiled_class_facts == 0) {
        return ();
    }
    alloc_locals;

    let compiled_class_fact = &compiled_class_facts[0];
    let compiled_class = compiled_class_fact.compiled_class;

    validate_entry_points(
        n_entry_points=compiled_class.n_external_functions,
        entry_points=compiled_class.external_functions,
    );

    validate_entry_points(
        n_entry_points=compiled_class.n_l1_handlers, entry_points=compiled_class.l1_handlers
    );
    // Compiled classes are expected to end with a `ret` opcode followed by a pointer to the
    // builtin costs.
    assert compiled_class.bytecode_ptr[compiled_class.bytecode_length] = 0x208b7fff7fff7ffe;
    assert compiled_class.bytecode_ptr[compiled_class.bytecode_length + 1] = cast(
        builtin_costs, felt
    );

    // Calculate the compiled class hash.
    // This hint enters a new scope that contains the bytecode segment structure of the class.
    %{ EnterScopeWithBytecodeSegmentStructure %}
    let (hash) = blake_compiled_class_hash(compiled_class, full_contract=FALSE);
    %{ LoadClass %}

    assert compiled_class_fact.hash = hash;

    return validate_compiled_class_facts(
        n_compiled_class_facts=n_compiled_class_facts - 1,
        compiled_class_facts=&compiled_class_facts[1],
        builtin_costs=builtin_costs,
    );
}
```
