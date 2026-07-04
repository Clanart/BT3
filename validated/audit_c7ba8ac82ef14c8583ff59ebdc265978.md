### Title
Missing Declaration Check in `execute_replace_class` Allows Permanent Contract Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract corresponds to a previously declared contract class. This is an explicit, acknowledged gap (marked with a TODO). A contract can replace its own class hash with an arbitrary, undeclared value. Because `execute_entry_point` subsequently uses that class hash to look up the compiled class — and will fail if the hash is not declared — any contract that undergoes such a replacement becomes permanently non-callable, freezing all funds held within it.

---

### Finding Description

In `execute_replace_class` the OS reads the caller-supplied `class_hash` directly from the syscall request and writes it into `contract_state_changes` with no validation:

```cairo
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
``` [1](#0-0) 

The TODO at line 898 explicitly acknowledges the missing check. [2](#0-1) 

When any subsequent call targets this contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [3](#0-2) 

If `class_hash` was replaced with an undeclared value, `dict_read` returns `0` (the default for an absent key), and `find_element` with `key=0` will fail to locate any compiled class, causing the OS to be unable to prove any block that includes a call to this contract. Every entry point — including withdrawal functions — becomes permanently unreachable.

The `replace_class` syscall is dispatched with the executing contract's own address, so it is self-targeted:

```cairo
if (selector == REPLACE_CLASS_SELECTOR) {
    execute_replace_class(contract_address=execution_context.execution_info.contract_address);
``` [4](#0-3) 

The analog to H-1 is direct: just as `commitCollateral` modified loan state without checking whether the loan was already active, `execute_replace_class` modifies contract class state without checking whether the new class is declared — a state-transition bypass that corrupts a live contract's reachability.

---

### Impact Explanation

Once a contract's class hash is replaced with an undeclared value, **all** calls to that contract fail at the OS level because `find_element` cannot resolve the compiled class. No entry point — including any fund-recovery or withdrawal function — can execute. All assets held in the contract's storage are permanently inaccessible.

This matches the allowed impact: **Critical — Permanent freezing of funds**.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any deployed contract during its own execution. Two realistic paths exist:

1. **Malicious contract pattern**: A contract deployer attracts user deposits, then triggers an internal `replace_class` call with an arbitrary undeclared hash (e.g., `felt(1)` or any value not in `contract_class_changes`). Because the OS does not reject this, the block is proven and the state is committed on-chain.

2. **Buggy contract pattern**: A contract with an unguarded upgrade path (e.g., an owner-callable `upgrade` function that forwards the class hash without validation) can be exploited by a malicious owner or, if access control is flawed, by any user.

The TODO comment confirms the check is intentionally absent from the current implementation, meaning the OS will accept any class hash value unconditionally.

---

### Recommendation

Inside `execute_replace_class`, before writing the new `StateEntry`, verify that `class_hash` is present in `contract_class_changes` (i.e., has been declared via a `declare` transaction). Concretely:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);  // Reject undeclared class hashes.
```

This mirrors the existing guard in `execute_declare_transaction`, which enforces `prev_value=0` to prevent re-declaration and uses `assert_not_zero(compiled_class_hash)` to reject zero hashes. [5](#0-4) 

---

### Proof of Concept

1. Attacker deploys `VaultContract` with a legitimate class hash `C_valid`. Users deposit ETH-equivalent tokens; the contract accumulates significant TVL.
2. `VaultContract` contains an internal function `_poison()` that calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (an arbitrary value never declared on-chain).
3. Attacker invokes `_poison()`. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef` from the request.
   - Skips the missing declaration check (TODO line 898).
   - Writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
4. The block is proven and accepted by the L1 verifier. On-chain state now records `VaultContract.class_hash = 0xdeadbeef`.
5. Any user attempts `withdraw()` on `VaultContract`. `execute_entry_point` calls `dict_read(contract_class_changes, key=0xdeadbeef)` → returns `0`. `find_element(..., key=0)` fails to locate a compiled class.
6. The call cannot be included in any provable block. All user funds are permanently frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-197)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
