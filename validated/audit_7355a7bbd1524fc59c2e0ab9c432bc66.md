### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Bricking — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the supplied `class_hash` corresponds to a previously declared class before committing the state change. This is directly analogous to the reported XCalls vulnerability, where a missing `isSupportedChain()` check allowed state to be mutated for unsupported destinations. Here, any contract can replace its own class hash with an arbitrary, undeclared felt value. Because the OS commits this to the `contract_state_changes` dictionary unconditionally, the contract becomes permanently unexecutable, freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the function `execute_replace_class` processes the `replace_class` syscall:

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
```

The developer-acknowledged TODO comment at line 898 explicitly states the missing check. The function reads `class_hash` directly from the syscall request and writes it into the contract's `StateEntry` without any validation that this hash exists in `contract_class_changes` (the declared class registry) or in the `compiled_class_facts_bundle`. The state update is unconditional and permanent.

Compare this to the XCalls report: `_xcall()` incremented `outXStreamOffset` for unsupported chains without calling `isSupportedChain()`. Here, `execute_replace_class` updates `class_hash` in `contract_state_changes` for an undeclared class without calling any equivalent "is declared" check.

---

### Impact Explanation

**Critical — Permanent Freezing of Funds.**

Once a contract's `class_hash` is set to an undeclared value, every subsequent call to that contract will fail at the class-lookup stage (the OS cannot find the compiled class for the stored hash). The contract becomes permanently unexecutable. Any ERC-20 tokens, ETH, or STRK held in that contract's storage are irrecoverably frozen. There is no recovery path: the contract cannot call `replace_class` again because execution of its entry points is impossible, and no external actor can override another contract's class hash.

If the affected contract is a widely-used token contract or a shared infrastructure contract, the impact scales to a large portion of the network's locked value.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract from within its own execution context. An unprivileged user can:

1. Deploy a contract that calls `replace_class` with an arbitrary felt (e.g., `1` or any random value not in the declared class set).
2. Trigger that contract's entry point via a standard `invoke` transaction.
3. The OS processes the syscall, writes the invalid class hash to state, and the contract is bricked.

No privileged role, leaked key, or social engineering is required. The entry path is a standard user-submitted transaction. The only prerequisite is gas to deploy and invoke the contract.

---

### Recommendation

Before committing the `dict_update` in `execute_replace_class`, add a validation that `class_hash` exists as a key in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the fix applied in the XCalls report: adding `require(isSupportedChain(destChainId), ...)` before the state-mutating operation.

---

### Proof of Concept

1. User submits an `invoke` transaction calling a contract `C` that executes:
   ```
   replace_class(class_hash=0xdeadbeef)  // 0xdeadbeef is not declared
   ```
2. The OS reaches `execute_replace_class` in `syscall_impls.cairo`.
3. At line 896, `class_hash = 0xdeadbeef` is read from the request.
4. The TODO check (line 898) is absent — no validation occurs.
5. `dict_update` at line 906 writes `StateEntry(class_hash=0xdeadbeef, ...)` for contract `C`.
6. The block is proven and finalized with this state root.
7. Any future call to contract `C` fails: the OS cannot find a compiled class for `0xdeadbeef` in `compiled_class_facts_bundle` or `contract_class_changes`.
8. All funds in `C`'s storage are permanently frozen. [1](#0-0)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-916)
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

    return ();
}
```
