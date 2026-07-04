### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Bricking - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The StarkNet OS `execute_replace_class` syscall handler directly writes an attacker-supplied class hash into a contract's `StateEntry` without verifying that the hash corresponds to a declared contract class. This is the exact structural analog of the reported "Transfer Ownership to incorrect address" pattern: a privileged state mutation is accepted without a validity check on the target value. A contract deployer can exploit this to permanently brick any contract they control (including their own account contract), irreversibly freezing all funds held by it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall:

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

The developer-acknowledged TODO at line 898 explicitly states the missing check. The function accepts any `felt` value as `class_hash` and writes it directly into `contract_state_changes` without consulting `contract_class_changes` (the dict that tracks declared class hashes).

The identical omission exists in the deprecated path: [2](#0-1) 

Both paths are reachable from the syscall dispatcher: [3](#0-2) 

**Why this causes permanent bricking**: When a subsequent transaction targets the affected contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,
);
``` [4](#0-3) 

If the class hash stored in `contract_state_changes` was never declared, `dict_read` returns 0 (the uninitialized default). `find_element` then searches for compiled class hash `0`, which does not exist in the bundle, causing an unrecoverable failure for every future call to that contract.

**Contrast with declare**: `execute_declare_transaction` enforces `prev_value=0` (class declared at most once) and verifies the Sierra component hash pre-image before writing to `contract_class_changes`. `execute_replace_class` performs neither check. [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

If the bricked contract is an account contract, its `__validate__` and `__execute__` entry points become permanently unreachable. All assets (ETH/STRK) held by that account are frozen with no recovery path, because:
- The class hash in `contract_state_changes` is immutably committed to the state Merkle tree after the block is proven.
- No future transaction can be validated from that account (validation itself requires executing the now-missing class).
- There is no protocol-level mechanism to override a committed class hash without a valid `replace_class` call, which itself requires executing the contract.

---

### Likelihood Explanation

**Medium.** The `replace_class` syscall is callable by any contract without privileged access — it is a standard user-facing syscall. A contract deployer can deploy a contract whose logic calls `replace_class` with an arbitrary undeclared hash (e.g., `1` or any random felt). The OS Cairo code, as written, will accept this state transition and commit it to the proven state. No operator collusion or key compromise is required; the attacker only needs to deploy and invoke a contract.

---

### Recommendation

Inside `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), add a lookup into `contract_class_changes` to assert the target class hash is declared before writing the new `StateEntry`:

```cairo
// Assert the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already present in `execute_entry_point` and closes the gap identified by the existing TODO comment.

---

### Proof of Concept

1. Deploy account contract `A` holding 100 STRK.
2. From `A`, submit an invoke transaction whose calldata causes `A`'s `__execute__` to call `replace_class(class_hash=0xdeadbeef)` where `0xdeadbeef` is never declared.
3. The OS processes the block: `execute_replace_class` writes `class_hash=0xdeadbeef` into `contract_state_changes[A]` with no validation.
4. The block is proven and the state root is updated; `A`'s class hash is now `0xdeadbeef` on-chain.
5. Any subsequent transaction from `A` reaches `execute_entry_point`, which calls `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0` → `find_element(..., key=0)` fails → transaction is permanently unexecutable.
6. The 100 STRK in `A` is frozen with no recovery path.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-910)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
```text
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;

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

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
