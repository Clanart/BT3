### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary `class_hash` from the caller and writes it directly into the contract's state entry without verifying that the provided hash corresponds to a previously declared class. This is the direct analog of the NFT withdrawal bug: just as `remove_nft_deposit` succeeded regardless of whether the `(pool_id, nft_token_id)` entry existed, `execute_replace_class` succeeds regardless of whether the target `class_hash` exists in `contract_class_changes`. Any contract can permanently freeze itself — and all funds it holds — by invoking `replace_class` with an undeclared hash.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function handles the `replace_class` syscall:

```cairo
func execute_replace_class{...}(contract_address: felt) {
    ...
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
    ...
}
```

The `class_hash` value comes directly from `request.class_hash`, which is attacker-controlled calldata. The OS writes this hash into `contract_state_changes` for the calling contract without performing any lookup into `contract_class_changes` to confirm the hash was previously declared via a `declare` transaction. The TODO comment at line 898 explicitly acknowledges this missing check.

The vulnerability class is identical to the NFT report: a state-mutating operation (`dict_update` on `contract_state_changes`) is performed without first verifying membership in the authoritative registry (`contract_class_changes`).

---

### Impact Explanation

After `replace_class` is called with an undeclared `class_hash`:

1. The contract's `class_hash` field in `contract_state_changes` is set to a hash that has no corresponding entry in `contract_class_changes`.
2. Any subsequent transaction targeting that contract address will attempt to load the class. Since the class does not exist in the declared-class registry, execution cannot proceed and will fail permanently.
3. All assets (ETH, STRK, or any ERC-20 tokens) held in the contract's storage become permanently inaccessible — the contract is bricked at the protocol level with no recovery path.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

- The `replace_class` syscall is reachable by any deployed contract without any privileged role. Any contract's execution can emit this syscall.
- A malicious actor can deploy a contract that accumulates user deposits (e.g., a fake vault or DEX), then call `replace_class` with an arbitrary undeclared hash to permanently freeze all deposited funds.
- The missing check is explicitly acknowledged in the source code with a `TODO` comment dated `1/1/2026`, confirming the developers are aware the validation is absent.
- No leaked keys, operator trust, or network-level attack is required — a single unprivileged transaction from the contract itself is sufficient.

---

### Recommendation

Before writing the new `class_hash` into `contract_state_changes`, verify that the hash exists in `contract_class_changes`. Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned value is non-zero (i.e., a valid compiled class hash was previously declared):

```cairo
// Verify the class has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the fix applied in the NFT report (`verify_nft_deposit` before `remove_nft_deposit`) and closes the analogous gap here.

---

### Proof of Concept

1. **Declare** a legitimate class `C` and **deploy** a contract `V` (vault) using class `C`. Users deposit funds into `V`.
2. The attacker (who controls `V`) crafts a transaction that invokes `V.__execute__`, which internally calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (an arbitrary undeclared hash).
3. The OS processes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef` is read from attacker-controlled calldata.
   - No lookup into `contract_class_changes` is performed (the TODO check is absent).
   - `dict_update` writes `StateEntry(class_hash=0xdeadbeef, ...)` for `V`'s address into `contract_state_changes`.
4. The block is proven and finalized. `V`'s on-chain class hash is now `0xdeadbeef`.
5. Any subsequent call to `V` (e.g., a user attempting to withdraw) causes the OS to attempt to load class `0xdeadbeef`, which does not exist in the class trie. Execution fails permanently.
6. All user funds in `V`'s storage are permanently frozen with no recovery mechanism.

**Root cause line:** [1](#0-0) 

**Acknowledged missing check (TODO comment):** [2](#0-1) 

**Syscall dispatch (attacker entry point):** [3](#0-2)

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
