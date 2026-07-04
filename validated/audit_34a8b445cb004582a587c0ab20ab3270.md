### Title
Unauthenticated Class Declaration via Unguarded BOOTSTRAP Path Bypasses `__validate_declare__` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

In `execute_declare_transaction`, the normal declare flow requires running the account contract's `__validate_declare__` entry point (signature verification) before registering a class hash. However, a special "BOOTSTRAP" code path at line 764 skips this authorization check entirely. Because the guard condition `sender_address == 'BOOTSTRAP'` is a plain felt-literal comparison with no cryptographic or privileged-role enforcement, any unprivileged transaction sender can satisfy it. An attacker can permanently register an arbitrary `compiled_class_hash` for any valid Sierra `class_hash` without owning the corresponding account, without a valid signature, and without paying fees.

---

### Finding Description

`execute_declare_transaction` in `transaction_impls.cairo` contains two distinct code paths:

**Normal path (lines 778–824):** Calls `check_and_increment_nonce`, reads the sender's on-chain state, runs `non_reverting_select_execute_entry_point_func` with selector `VALIDATE_DECLARE_ENTRY_POINT_SELECTOR` (i.e., `__validate_declare__`), then calls `charge_fee`. This enforces that only the legitimate account owner — who can produce a valid signature — can declare a class.

**BOOTSTRAP path (lines 761–776):** When `sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3` and `max_possible_fee == 0`, the OS skips nonce verification, skips `__validate_declare__`, skips fee charging, and directly calls `dict_update` to register `class_hash → compiled_class_hash`.

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

The string `'BOOTSTRAP'` in Cairo is the felt encoding of the ASCII bytes, confirmed by the test:

```rust
assert_eq!("BOOTSTRAP", as_cairo_short_string(&num).unwrap());
``` [2](#0-1) 

and the constant definition:

```rust
pub fn bootstrap_address() -> ContractAddress {
    // A felt representation of the string 'BOOTSTRAP'.
    ContractAddress::from(0x424f4f545354524150_u128)
}
``` [3](#0-2) 

There is no cryptographic proof, privileged-role check, or on-chain access-control list guarding who may use this address. Any transaction sender can set `sender_address = 0x424f4f545354524150`, `nonce = 0`, `version = 3`, and all resource bounds to zero to satisfy every condition.

The normal path that the BOOTSTRAP path bypasses: [4](#0-3) 

The `__validate_declare__` runner that is skipped: [5](#0-4) 

---

### Impact Explanation

**Critical — Direct loss of funds / Permanent freezing of funds.**

The `dict_update` call uses `prev_value=0`, meaning a class hash can be declared **only once**: [6](#0-5) 

An attacker who front-runs a legitimate class declaration can permanently bind a valid Sierra `class_hash` to an attacker-chosen `compiled_class_hash`. Because the Sierra class hash is verified by `finalize_class_hash` but the CASM mapping is not verified to be the correct one for that Sierra source, the attacker can supply a different (malicious or broken) CASM. Every contract subsequently deployed from that class hash will execute the wrong CASM:

- **Direct loss of funds**: if the attacker's CASM contains logic that redirects token transfers or bypasses access controls.
- **Permanent freezing of funds**: the legitimate CASM can never be registered (the slot is taken), so contracts depending on the correct class are permanently broken and any funds locked in them are irrecoverable.

---

### Likelihood Explanation

**High.** The attacker-controlled entry path requires only:

1. Setting `sender_address = 0x424f4f545354524150` (the felt for `'BOOTSTRAP'`).
2. Setting `nonce = 0`.
3. Setting `version = 3`.
4. Setting all resource bounds to zero (making `max_possible_fee = 0`).

No privileged key, no leaked secret, no operator collusion, and no Sybil attack is needed. The conditions are fully deterministic and trivially satisfiable by any transaction sender. The attack is especially effective as a front-run against any legitimate class declaration.

---

### Recommendation

The BOOTSTRAP path must enforce that the `sender_address` is a genuinely privileged entity — not merely a felt literal that any user can impersonate. Options include:

1. **Remove the BOOTSTRAP path entirely** once the system is bootstrapped, or gate it behind a block-number deadline enforced in the OS (e.g., only valid in block 0).
2. **Require a cryptographic proof of authority**: verify a sequencer/operator signature over the bootstrap transaction inside the OS, analogous to how `__validate_declare__` verifies account signatures.
3. **Enforce the check at the OS config level**: include the bootstrap address in the `StarknetOsConfig` and validate it against a known public key, rather than a plain felt comparison.

The fix must be in the Cairo OS code (the audited scope), not only in off-chain gateway filters, because the OS proof is the source of truth for state validity.

---

### Proof of Concept

An attacker constructs and submits the following declare transaction to the sequencer:

```
sender_address  = 0x424f4f545354524150   // felt('BOOTSTRAP')
version         = 3
nonce           = 0
resource_bounds = { l1_gas: 0, l2_gas: 0, l1_data_gas: 0 }  // max_possible_fee == 0
class_hash      = <target legitimate Sierra class hash>
compiled_class_hash = <attacker-chosen CASM hash, different from the correct one>
signature       = []  // empty — __validate_declare__ is never called
```

When the OS processes this transaction in `execute_declare_transaction`:

1. `sender_address == 'BOOTSTRAP'` → **true** (line 764).
2. `tx_info.nonce == 0` → **true** (line 764).
3. `tx_info.version == 3` → **true** (line 764).
4. `compute_max_possible_fee(tx_info)` returns `0` (line 765–766).
5. The OS skips `check_and_increment_nonce`, skips `__validate_declare__`, skips `charge_fee`.
6. `dict_update` permanently registers `class_hash → attacker_compiled_class_hash` (lines 770–772).

The legitimate owner can never re-declare the same class hash (the `prev_value=0` constraint will fail). All contracts deployed from this class hash will execute the attacker's CASM, enabling fund theft or permanent fund freezing. [7](#0-6)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L778-824)
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
```

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L994-997)
```rust
fn test_bootstrap_address() {
    let num = *ApiExecutableDeclareTransaction::bootstrap_address().0.key();
    assert_eq!("BOOTSTRAP", as_cairo_short_string(&num).unwrap());
}
```

**File:** crates/starknet_api/src/executable_transaction.rs (L240-243)
```rust
    pub fn bootstrap_address() -> ContractAddress {
        // A felt representation of the string 'BOOTSTRAP'.
        ContractAddress::from(0x424f4f545354524150_u128)
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L110-159)
```text
// Runs the account contract's "__validate__" entry point, which is responsible for
// signature verification.
//
// Arguments:
// block_context - a global context that is fixed throughout the block.
// tx_execution_context - The execution context of the underlying invoke transaction.
func run_validate{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, tx_execution_context: ExecutionContext*) {
    alloc_locals;
    local tx_execution_info: ExecutionInfo* = tx_execution_context.execution_info;

    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }

    // "__validate__" is expected to get the same calldata as "__execute__".
    local validate_execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=tx_execution_context.class_hash,
        calldata_size=tx_execution_context.calldata_size,
        calldata=tx_execution_context.calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_validate,
            tx_info=tx_execution_info.tx_info,
            caller_address=tx_execution_info.caller_address,
            contract_address=tx_execution_info.contract_address,
            selector=VALIDATE_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=tx_execution_context.deprecated_tx_info,
    );

    // The __validate__ function should not revert.
    let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
        block_context=block_context, execution_context=validate_execution_context
    );
    if (is_deprecated == 0) {
        %{ CheckRetdataForDebug %}
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }

    return ();
}
```
