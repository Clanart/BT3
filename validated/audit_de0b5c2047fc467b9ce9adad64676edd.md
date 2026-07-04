### Title
Unauthorized Class Declaration via Unprivileged `'BOOTSTRAP'` Sender Address Bypass - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `execute_declare_transaction` function in the StarkNet OS contains a bootstrap shortcut path that skips all authorization checks — including `__validate_declare__`, fee payment, and nonce management — when the `sender_address` field of a declare transaction equals the felt literal `'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_possible_fee == 0`. Because `'BOOTSTRAP'` is simply the ASCII encoding of the string as a Cairo felt (a public, predictable numeric value), any unprivileged user can craft a declare transaction satisfying these conditions and have the OS accept it as valid without any account ownership, signature, or fee.

This is a direct analog of the ENS M-01 vulnerability: just as approval on the wrapper contract (a different permission domain) over-extended to allow wrapping of unwrapped domains, here the `sender_address == 'BOOTSTRAP'` check (a magic felt value, not a privileged identity) over-extends to allow class declaration without any account authorization.

---

### Finding Description

In `execute_declare_transaction`, after computing and asserting the transaction hash, the OS evaluates the following condition:

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

When this branch is taken, the OS:

1. **Skips `check_and_increment_nonce`** — no nonce validation or increment occurs.
2. **Skips `__validate_declare__`** — no account signature verification is performed.
3. **Skips `charge_fee`** — no fee is deducted from any account.
4. **Directly writes `class_hash → compiled_class_hash`** into `contract_class_changes`.

The normal (non-bootstrap) path enforces all three of these checks:

```cairo
check_and_increment_nonce(tx_info=tx_info);
// ... run __validate_declare__ ...
dict_update{dict_ptr=contract_class_changes}(key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash);
charge_fee(...);
``` [2](#0-1) 

The critical flaw is that `'BOOTSTRAP'` in Cairo is simply the ASCII-encoded felt of the string `"BOOTSTRAP"` — a fixed, publicly known numeric value. The OS performs no privileged-identity check: it does not verify that `sender_address` corresponds to a deployed contract, a protocol-controlled address, or any special on-chain entity. Any user who submits a declare transaction with `sender_address = felt('BOOTSTRAP')`, `nonce = 0`, `version = 3`, and all resource bounds set to zero satisfies the condition.

The transaction hash is computed over `sender_address` (among other fields), so the hash assertion passes correctly for any such crafted transaction:

```cairo
let transaction_hash = compute_declare_transaction_hash(
    common_fields=common_tx_fields,
    class_hash=[class_hash_ptr],
    compiled_class_hash=compiled_class_hash,
    ...
);
%{ AssertTransactionHash %}
``` [3](#0-2) 

The hash assertion is not a guard against this attack — it only confirms internal consistency of the transaction fields, not that the sender is authorized.

---

### Impact Explanation

**Direct loss of funds (Critical):**

1. **Fee bypass**: An attacker can declare an unbounded number of class hashes without paying any fees. The sequencer receives no compensation for the state changes and proof work, representing a direct economic loss to the network.

2. **Front-running legitimate class declarations**: Because `dict_update` enforces `prev_value=0` (a class may be declared only once), an attacker who observes a pending legitimate declare transaction can race to declare the same `class_hash` first — mapping it to a different `compiled_class_hash`. The legitimate declarer's subsequent transaction will then fail (the `prev_value=0` assertion reverts). Users who later deploy contracts under the attacker-declared `class_hash` will execute the attacker's chosen compiled class, not the intended one. If those contracts handle user funds, this results in direct loss of funds.

3. **Unauthorized state mutation**: The OS's class registry is mutated without any account authorization, violating the account-abstraction security model that underpins all StarkNet transaction validity.

---

### Likelihood Explanation

The conditions required are entirely attacker-controlled and require no privileged access:

- `sender_address = felt('BOOTSTRAP')` — a fixed, publicly known felt value.
- `nonce = 0` — trivially satisfiable.
- `version = 3` — the current standard transaction version.
- All resource bounds set to zero — trivially satisfiable.

The attacker only needs to submit a well-formed declare transaction to the network. The OS will accept it as provably valid. No leaked keys, no malicious operator, and no trusted role is required on the attacker's side. The only external dependency is that the sequencer includes the transaction in a block; since the OS accepts it as valid, a sequencer that relies on OS-level validity (rather than its own allowlist) would include it.

---

### Recommendation

Remove the bootstrap shortcut entirely, or gate it behind a verifiable on-chain privileged identity (e.g., a specific protocol-controlled contract address stored in `block_context` or `os_global_context`) rather than a magic felt string literal. The current check `sender_address == 'BOOTSTRAP'` provides no real access control because any user can set `sender_address` to this value in a declare transaction.

If the bootstrap mechanism is genuinely needed for system initialization, it should be enforced at the block-context level (e.g., a boolean flag in `BlockContext` set only for the genesis block) rather than as a per-transaction sender address check.

---

### Proof of Concept

1. Compute `felt('BOOTSTRAP')` — the ASCII encoding of `"BOOTSTRAP"` as a Cairo felt (this is a fixed, public value).
2. Obtain or construct a valid Sierra class (to produce a valid `class_hash` via `finalize_class_hash`) and its corresponding `compiled_class_hash`.
3. Craft a declare transaction with:
   - `sender_address = felt('BOOTSTRAP')`
   - `nonce = 0`
   - `version = 3`
   - All resource bounds (`L1_GAS`, `L2_GAS`, `L1_DATA_GAS`) with `max_amount = 0` and `max_price_per_unit = 0` (so `compute_max_possible_fee` returns 0)
   - The chosen `class_hash` and `compiled_class_hash`
4. Compute the transaction hash using `compute_declare_transaction_hash` with these fields — the hash assertion in the OS will pass.
5. Submit the transaction. The OS executes the bootstrap branch: it skips `__validate_declare__`, skips `charge_fee`, skips `check_and_increment_nonce`, and writes `class_hash → compiled_class_hash` directly into `contract_class_changes`.
6. The class is now declared in the StarkNet state without any account ownership, signature, or fee payment. [1](#0-0) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L693-710)
```text
func execute_declare_transaction{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*) {
    alloc_locals;

    local tx_version;
    %{ TxVersion %}
    if (tx_version == 0) {
        %{ SkipTx %}
        return ();
    }

    // Guess transaction fields.
    local sender_address;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L724-732)
```text
        // Compute transaction hash.
        let transaction_hash = compute_declare_transaction_hash(
            common_fields=common_tx_fields,
            class_hash=[class_hash_ptr],
            compiled_class_hash=compiled_class_hash,
            account_deployment_data_size=account_deployment_data_size,
            account_deployment_data=account_deployment_data,
        );
        %{ AssertTransactionHash %}
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L779-824)
```text
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
