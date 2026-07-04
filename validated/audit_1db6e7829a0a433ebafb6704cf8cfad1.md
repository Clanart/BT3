### Title
Unauthenticated Bootstrap Declare Path Allows Arbitrary `compiled_class_hash` Registration Without Signature Validation — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special bootstrap path that, when triggered by a transaction with `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_fee == 0`, entirely skips signature validation and directly writes an attacker-controlled `compiled_class_hash` into the global class registry. The `compiled_class_hash` is only checked to be non-zero. Any unprivileged class declarer can craft such a transaction, front-run a legitimate bootstrap declaration, and permanently poison the class registry for a given Sierra class hash.

---

### Finding Description

In `execute_declare_transaction`, lines 764–776 implement a bootstrap shortcut:

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

This path:

1. **Skips `check_and_increment_nonce`** — the nonce at `'BOOTSTRAP'` is never incremented, so the same nonce=0 condition can be reused for any class hash.
2. **Skips `run_validate`** — no `__validate_declare__` entry point is called; no ECDSA signature is verified.
3. **Skips `charge_fee`** — no economic cost to the attacker.
4. **Accepts any non-zero `compiled_class_hash`** — the only guard is `assert_not_zero(compiled_class_hash)`. [2](#0-1) 

The `class_hash` itself is verified via `finalize_class_hash` (it must be a valid Sierra class hash pre-image), but the `compiled_class_hash` — the pointer to the actual executable bytecode — is completely unvalidated beyond being non-zero. [3](#0-2) 

The `sender_address` field is loaded from the hint `%{ DeclareTxFields %}` and committed to by the transaction hash (verified via `%{ AssertTransactionHash %}`). Since `'BOOTSTRAP'` is a public felt literal (`0x424f4f545354524150`), any party can craft a declare transaction with this sender address, a valid Sierra class pre-image, and an arbitrary non-zero `compiled_class_hash`. The OS will accept it through the bootstrap path without any authorization check. [4](#0-3) 

The `dict_update` call uses `prev_value=0`, meaning a class can only be declared once. This is the mechanism that makes front-running destructive: once the attacker's poisoned entry is written, the legitimate declaration will fail with a dict mismatch. [5](#0-4) 

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

The `contract_class_changes` dict maps `class_hash → compiled_class_hash`. During execution, the OS resolves the compiled bytecode by looking up the stored `compiled_class_hash`. If an attacker registers a critical system class (e.g., the fee token, a universal account class) with a `compiled_class_hash` of `1` (non-zero but matching no real compiled class), every subsequent transaction that invokes that class will fail at the bytecode-lookup stage. Because fee charging itself calls the fee token class: [6](#0-5) 

a poisoned fee token class hash causes `charge_fee` to fail for every transaction, halting the network's ability to confirm new transactions.

---

### Likelihood Explanation

**Medium.** The bootstrap path is intended for the sequencer to declare system classes during network initialization — a narrow, predictable window. An attacker who monitors the mempool or the block-building process can:

1. Observe a pending bootstrap declare transaction for a known system class hash.
2. Craft a competing transaction with the same `class_hash` but `compiled_class_hash = 1`.
3. Submit it with higher priority (or collude with a block producer) to have it land first.

The `sender_address = 'BOOTSTRAP'` condition is a public constant; no key material or privileged access is required to construct the transaction. The only constraint is timing relative to the legitimate bootstrap transaction.

---

### Recommendation

1. **Bind the bootstrap path to a sequencer-controlled address or a governance-controlled allowlist**, rather than a public felt literal. For example, verify that `sender_address` matches a value stored in `block_context.os_global_context` (e.g., `sequencer_address`), so only the sequencer can trigger the bootstrap path.

2. **Validate `compiled_class_hash` against the Sierra class pre-image** during the bootstrap path, analogous to how `finalize_class_hash` validates the Sierra class hash. The compiled class hash should be verified to correspond to a known, well-formed compiled class before being written to state.

3. **Increment the nonce** even in the bootstrap path, or use a dedicated per-class-hash lock, to prevent replay of the same bootstrap conditions across multiple class declarations.

---

### Proof of Concept

**Attacker-controlled entry path:**

1. The network is bootstrapping. The sequencer prepares a declare transaction:
   - `sender_address = 'BOOTSTRAP'`
   - `class_hash = H` (hash of the fee token Sierra class)
   - `compiled_class_hash = C` (correct compiled class hash)
   - `nonce = 0`, `version = 3`, all resource bounds = 0

2. The attacker observes this pending transaction and crafts:
   - `sender_address = 'BOOTSTRAP'`
   - `class_hash = H` (same Sierra class hash — valid pre-image required, but the Sierra source is public)
   - `compiled_class_hash = 1` (non-zero, but invalid)
   - `nonce = 0`, `version = 3`, all resource bounds = 0

3. The attacker's transaction is included first. The OS executes `execute_declare_transaction`:
   - `finalize_class_hash` verifies the Sierra pre-image → passes (class hash is valid).
   - Bootstrap condition is met → `run_validate` is skipped.
   - `assert_not_zero(1)` → passes.
   - `dict_update(key=H, prev_value=0, new_value=1)` → class `H` is now registered with `compiled_class_hash = 1`. [7](#0-6) 

4. The sequencer's legitimate bootstrap transaction arrives. `dict_update(key=H, prev_value=0, ...)` fails because `prev_value` is now `1`, not `0`. The legitimate declaration is permanently blocked.

5. Any subsequent transaction that invokes the fee token (class hash `H`) causes the OS to look up compiled class with hash `1`. No such compiled class exists. `charge_fee` fails for every transaction. The network halts. [8](#0-7)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L138-163)
```text
    local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
    let (fee_state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=fee_token_address
    );
    let (__fp__, _) = get_fp_and_pc();
    // Use block_info directly from block_context, so that charge_fee will always run in
    // execute-mode rather than validate-mode.
    local execution_context: ExecutionContext = ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=fee_state_entry.class_hash,
        calldata_size=TransferCallData.SIZE,
        calldata=&calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_execute,
            tx_info=tx_info,
            caller_address=tx_info.account_contract_address,
            contract_address=fee_token_address,
            selector=TRANSFER_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=tx_execution_context.deprecated_tx_info,
    );

    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L710-720)
```text
    local sender_address;
    local class_hash_ptr: felt*;
    local compiled_class_hash;
    local account_deployment_data_size;
    local account_deployment_data: felt*;
    %{ DeclareTxFields %}
    let common_tx_fields = get_account_tx_common_fields(
        block_context=block_context,
        tx_hash_prefix=DECLARE_HASH_PREFIX,
        sender_address=sender_address,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L735-743)
```text
        local contract_class_component_hashes: ContractClassComponentHashes*;
        %{ SetComponentHashes %}

        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L764-776)
```text
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
