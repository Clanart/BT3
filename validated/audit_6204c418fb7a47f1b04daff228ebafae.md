### Title
Missing Declared Class Validation in `replace_class` Syscall Enables Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts any arbitrary class hash in the `replace_class` syscall without verifying that the hash corresponds to a declared contract class. An unprivileged contract can call `replace_class` with an undeclared hash, permanently setting its own class hash to a value with no corresponding compiled class. All future calls to that contract will fail, permanently freezing any funds it holds.

---

### Finding Description

In `execute_replace_class`, the OS updates the calling contract's class hash in `contract_state_changes` using the caller-supplied `request.class_hash` with no validation that the hash is declared. The code itself contains an explicit TODO acknowledging the missing check:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}

tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
);

dict_update{dict_ptr=contract_state_changes}(
    key=contract_address,
    prev_value=cast(state_entry, felt),
    new_value=cast(new_state_entry, felt),
);
``` [1](#0-0) 

Once this state is committed, any subsequent call to the contract enters `execute_entry_point`, which performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
``` [2](#0-1) 

For an undeclared class hash, `dict_read` returns the default value (0). `find_element` then searches the compiled class facts bundle for a compiled class with hash 0, which does not exist, causing every future call to the contract to fail irrecoverably.

The `contract_class_changes` dict is initialized per-block in `initialize_state_changes` and squashed into persistent state at the end of each block via `state_update`. Once the block containing the bad `replace_class` is finalized, the contract's class hash in the Merkle state tree is permanently set to the undeclared value. [3](#0-2) 

---

### Impact Explanation

Any ETH, ERC20 tokens, or other assets stored in the contract's storage slots become permanently inaccessible. The contract cannot be called, upgraded, or recovered because every entry point dispatch fails at the compiled-class lookup step. This is **Critical: Permanent freezing of funds**.

---

### Likelihood Explanation

The `replace_class` syscall is available to every Sierra contract with no privilege requirement. The attack surface includes:

- Contracts with on-chain governance or upgrade mechanisms where a malicious proposal can supply an undeclared class hash.
- Contracts that compute the new class hash dynamically (e.g., from calldata or storage) without off-chain validation.
- Any buggy contract that accidentally passes an invalid hash.

Because the OS provides no safety net, a single successful call permanently bricks the contract. Stock-split-style external state changes (e.g., a class being removed from the declared set, or a hash computed incorrectly) map directly to this scenario.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify that it is present in `contract_class_changes` (i.e., has a non-zero compiled class hash entry). Concretely, perform a `dict_read` on `contract_class_changes` for `request.class_hash` and assert the result is non-zero before proceeding with the `dict_update`.

---

### Proof of Concept

1. Deploy contract `C` holding 100 ETH. `C` exposes `upgrade(new_class_hash: felt)` which calls the `replace_class` syscall with the supplied hash.
2. Submit an invoke transaction calling `C.upgrade(0xdeadbeef)` where `0xdeadbeef` is not a declared class hash.
3. The OS executes `execute_replace_class`: no declared-class check is performed; `contract_state_changes[C].class_hash` is set to `0xdeadbeef`.
4. The block is finalized; the Merkle state tree now records `C`'s class hash as `0xdeadbeef`.
5. Any subsequent call to `C` reaches `execute_entry_point`, performs `dict_read(contract_class_changes, 0xdeadbeef)` → returns 0, then `find_element(..., key=0)` finds no compiled class → execution fails.
6. The 100 ETH in `C`'s storage is permanently frozen with no recovery path.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-913)
```text
    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-166)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L261-276)
```text
// Initializes state changes dictionaries.
func initialize_state_changes() -> (
    contract_state_changes: DictAccess*, contract_class_changes: DictAccess*
) {
    %{ InitializeStateChanges %}
    // A dictionary from contract address to a dict of storage changes of type StateEntry.
    let (contract_state_changes: DictAccess*) = dict_new();

    %{ InitializeClassHashes %}
    // A dictionary from class hash to compiled class hash (Casm).
    let (contract_class_changes: DictAccess*) = dict_new();

    return (
        contract_state_changes=contract_state_changes, contract_class_changes=contract_class_changes
    );
}
```
