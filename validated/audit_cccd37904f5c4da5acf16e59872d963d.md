### Title
`execute_replace_class` Accepts Undeclared/Zero Class Hash, Permanently Freezing Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the replacement class hash is a declared class. A contract can replace its own class hash with `0` or any arbitrary undeclared value. Once the class hash is set to an undeclared value, the contract becomes permanently non-callable (no entry points can be resolved), while all funds stored in its storage remain permanently frozen. This is the direct analog of the reported H-03 pattern: a resource-holding object is "destroyed" (made permanently inaccessible) by zeroing one field, without checking whether the object still holds assets.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 878–916), the OS processes the `replace_class` syscall by reading `request.class_hash` and writing it directly into the contract's `StateEntry` without any validation:

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
```

The in-code `TODO` at line 898 explicitly acknowledges the missing check:

> `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`

The OS accepts any felt value as the new class hash — including `0` and any hash that has never been declared via a `declare` transaction. After the state update is committed, the contract's `StateEntry.class_hash` is set to the invalid value. When any future transaction attempts to call into this contract, the OS will look up the class hash in the compiled class facts bundle and find no matching entry, making the contract permanently non-callable. The contract's storage (which may contain token balances, vault shares, or other assets) remains in the global state tree but is forever inaccessible.

The parallel to H-03 is exact:

| H-03 (ShortToken) | StarkNet OS analog |
|---|---|
| `shortAmount == 0` triggers `_burn()` | `replace_class(0)` sets `class_hash = 0` |
| `collateralAmount` not checked before burn | `storage_ptr` (funds) not checked before class replacement |
| Position owner loses all remaining collateral | Contract users lose all funds stored in contract storage |
| Burned token has no owner; collateral is stuck | Contract has no callable class; storage is stuck |

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` is set to `0` or an undeclared hash, no transaction can successfully execute any entry point on that contract. The contract's storage — which may contain ERC-20 balances, LP shares, collateral deposits, or any other on-chain assets — is permanently inaccessible. There is no recovery mechanism: the StarkNet OS has no `selfdestruct`-equivalent that returns funds, and the state tree will retain the frozen storage indefinitely. Any funds held in the contract's storage at the time of the invalid `replace_class` call are permanently lost to their owners.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract on itself (the OS uses `execution_context.execution_info.contract_address` as the target). Realistic triggering paths include:

1. **Malicious contract deployer (rug-pull):** A deployer creates a contract that accumulates user funds (vault, AMM, staking contract), then calls `replace_class(0)` to permanently freeze all deposited assets. The deployer is an unprivileged transaction sender.

2. **Contract bug / reentrancy:** A contract with a vulnerability that allows an attacker to invoke arbitrary internal calls can be exploited to call `replace_class(0)`. The attacker cannot drain funds directly (e.g., due to access controls on withdrawal functions) but can freeze them via this unguarded syscall.

3. **User mistake:** A contract developer testing upgrade logic calls `replace_class` with a hash that has not yet been declared, permanently bricking the contract and any funds inside it.

All three paths are reachable by unprivileged actors (transaction senders, contract deployers) without any privileged key or operator access.

---

### Recommendation

Add a validation step inside `execute_replace_class` to assert that `request.class_hash` corresponds to a previously declared class (i.e., it exists in `contract_class_changes` or the pre-existing class tree) before committing the state update. The existing `TODO` comment already identifies this as a known gap. The fix should resolve the TODO:

```diff
 let class_hash = request.class_hash;

-// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
+// Validate that the replacement class hash is a declared class.
+let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
+with_attr error_message("replace_class: class hash is not declared.") {
+    assert_not_zero(compiled_class_hash);
+}
 local state_entry: StateEntry*;
```

---

### Proof of Concept

**Scenario: Attacker freezes vault funds via `replace_class(0)`**

1. Attacker deploys a vault contract (class `V`) that accepts deposits from users. Users deposit 1,000 STRK into the vault. The vault's storage now holds `balance[user] = 1000`.

2. The vault contract has a public `upgrade(new_class_hash)` function with insufficient access control, or the attacker is the deployer.

3. Attacker calls `upgrade(0)`, which internally calls the `replace_class` syscall with `class_hash = 0`.

4. The OS executes `execute_replace_class`:
   - Reads `request.class_hash = 0`.
   - Skips the missing validation (no check that `0` is a declared class).
   - Writes `new StateEntry(class_hash=0, storage_ptr=..., nonce=...)` into `contract_state_changes`.

5. After the block is committed, the vault's `StateEntry.class_hash = 0`.

6. Any future transaction calling `vault.withdraw()` causes the OS to look up class `0` in the compiled class facts bundle. No entry is found. The transaction fails.

7. The 1,000 STRK stored in the vault's storage is permanently frozen. The `storage_ptr` in the `StateEntry` still points to the storage dict containing the balances, but no callable entry point exists to access them.

**Root cause location:** [1](#0-0) 

**The unguarded class hash write (no declared-class check before `dict_update`):** [2](#0-1) 

**The `StateEntry` structure showing that `storage_ptr` (funds) is preserved while `class_hash` is overwritten:** [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/squash.cairo (L60-66)
```text
    assert [squashed_prev_state] = StateEntry(
        class_hash=prev_state.class_hash, storage_ptr=squashed_storage_ptr, nonce=prev_state.nonce
    );

    local squashed_new_state: StateEntry* = new StateEntry(
        class_hash=new_state.class_hash, storage_ptr=squashed_storage_ptr_end, nonce=new_state.nonce
    );
```
