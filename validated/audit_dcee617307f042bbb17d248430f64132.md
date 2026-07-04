### Title
Front-Running Attack on `execute_declare_transaction` Enables Permanent Class Poisoning via Unvalidated `compiled_class_hash` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` enforces a first-come-first-served class registration using `dict_update(..., prev_value=0, new_value=compiled_class_hash)`. The OS verifies that `class_hash` is a valid Sierra class hash but performs **no validation** that `compiled_class_hash` corresponds to the actual compiled (CASM) class for that Sierra class. An unprivileged attacker who observes a pending declare transaction in the mempool can front-run it with the same `class_hash` but an arbitrary non-zero `compiled_class_hash`, permanently poisoning the class entry. Any contract subsequently deployed under that class will be unexecutable, permanently freezing any funds held within.

---

### Finding Description

In `execute_declare_transaction`, the OS performs two checks on the class being declared:

1. **Sierra class hash verification** (lines 738–743): The OS recomputes `expected_class_hash` from `contract_class_component_hashes` and asserts it equals `[class_hash_ptr]`. This ensures the Sierra class hash is a valid commitment to the Sierra program.

2. **`compiled_class_hash` check** (line 816): The OS only asserts `assert_not_zero(compiled_class_hash)`. There is **no verification** that `compiled_class_hash` is the correct CASM hash for the declared Sierra class.

The class is then registered with a first-come-first-served guard:

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
```

This is structurally identical to the `enrollCourier()` pattern: `require(couriers[id].cut == 0)` followed by `couriers[id] = Courier(msg.sender, cut)`. The first transaction to write wins, and subsequent attempts with `prev_value=0` will fail.

Because `compiled_class_hash` is included in the transaction hash (line 287–288 of `transaction_hash.cairo`) and signed by the attacker's own account, the attacker's transaction is cryptographically valid from the OS's perspective. The OS has no mechanism to reject it.

The `validate_compiled_class_facts` function (in `compiled_class.cairo`, lines 99–138) validates that compiled class facts loaded for execution match their own hashes, but it does **not** cross-check those hashes against the `compiled_class_hash` values stored in `contract_class_changes` during declare transactions. The two validation paths are entirely disconnected.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once an attacker front-runs the declare transaction and registers `class_hash = X` with `compiled_class_hash = Z` (an arbitrary non-zero value with no corresponding valid compiled class), the slot is permanently occupied (`prev_value=0` guard prevents re-declaration). Any contract subsequently deployed using class `X` will reference `compiled_class_hash = Z` in the class tree. Since no valid compiled class with hash `Z` exists, execution of those contracts cannot produce a valid proof. Funds held in such contracts are permanently inaccessible.

---

### Likelihood Explanation

**Medium.** The attacker requires:
1. An existing account contract (standard prerequisite for any declare transaction).
2. Visibility of the victim's pending declare transaction (standard mempool observation).
3. Submission of a valid declare transaction with the same `class_hash` and an arbitrary non-zero `compiled_class_hash`, signed by the attacker's own account.

No privileged access, leaked keys, or malicious operator behavior is required. The attack is especially relevant for high-value protocol classes (e.g., token contracts, multisigs) where the class is declared before deployment and funds are subsequently locked.

---

### Recommendation

The OS should validate that `compiled_class_hash` in a declare transaction is the correct CASM hash for the declared Sierra class. Concretely, the `compiled_class_hash` provided in the declare transaction should be cross-checked against the hash computed by `validate_compiled_class_facts` (or an equivalent inline computation) before being written to `contract_class_changes`. This closes the gap between the two currently disconnected validation paths and eliminates the ability to register an arbitrary `compiled_class_hash` for a given `class_hash`.

---

### Proof of Concept

1. **Victim** constructs a declare transaction: `class_hash = X` (valid Sierra class), `compiled_class_hash = Y` (correct CASM hash), signed by victim's account `A`.

2. **Attacker** observes the pending transaction, constructs their own declare transaction: `class_hash = X` (same Sierra class — its hash is public), `compiled_class_hash = Z` (arbitrary non-zero felt, e.g., `1`), signed by attacker's account `B`. This transaction has a distinct transaction hash (different sender, different `compiled_class_hash`) and is fully valid per OS rules.

3. Attacker submits with higher priority. The sequencer includes the attacker's transaction first.

4. The OS executes `execute_declare_transaction` for the attacker's tx:
   - Line 742: `assert [class_hash_ptr] = expected_class_hash` — passes (same Sierra class).
   - Line 816: `assert_not_zero(compiled_class_hash)` — passes (`Z != 0`).
   - Lines 817–819: `dict_update(..., prev_value=0, new_value=Z)` — succeeds; class `X` is now registered with `compiled_class_hash = Z`.

5. The OS executes `execute_declare_transaction` for the victim's tx:
   - Lines 817–819: `dict_update(..., prev_value=0, new_value=Y)` — **fails** because `prev_value` is now `Z`, not `0`. The victim's transaction is rejected.

6. Class `X` is permanently declared with `compiled_class_hash = Z`. Any protocol that deploys contracts under class `X` will find those contracts unexecutable. Funds deposited into such contracts are permanently frozen.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L264-291)
```text
func compute_declare_transaction_hash{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    common_fields: CommonTxFields*,
    class_hash: felt,
    compiled_class_hash: felt,
    account_deployment_data_size: felt,
    account_deployment_data: felt*,
) -> felt {
    alloc_locals;

    // TODO(Noa, 01/01/2026): remove the following `assert` once the field is supported.
    assert account_deployment_data_size = 0;
    with_attr error_message("Invalid transaction version: {version}.") {
        assert common_fields.version = 3;
    }

    let hash_state: PoseidonHashState = poseidon_hash_init();
    with hash_state {
        hash_tx_common_fields(common_fields=common_fields);
        poseidon_hash_update_with_nested_hash(
            data_ptr=account_deployment_data, data_length=account_deployment_data_size
        );
        // Add the class hash to the hash state.
        poseidon_hash_update_single(item=class_hash);
        poseidon_hash_update_single(item=compiled_class_hash);
    }
    let transaction_hash = poseidon_hash_finalize(hash_state=hash_state);

    return transaction_hash;
```
