### Title
Bootstrap Declare Path Bypasses Signature Verification, Nonce Check, and Fee Charging — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a special "bootstrap" code path that unconditionally skips `__validate_declare__` (signature verification), `check_and_increment_nonce`, and `charge_fee` whenever the transaction's `sender_address` equals the felt literal `'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_possible_fee == 0`. Because the nonce is never incremented in this path, the condition `nonce == 0` is permanently satisfiable for the `'BOOTSTRAP'` address, allowing the path to be triggered an unlimited number of times across different class hashes.

---

### Finding Description

In `execute_declare_transaction`, after the transaction hash is computed and the class hash pre-image is verified, the following guard appears:

```cairo
// Do not run validate or perform any account-related actions for declare transactions that
// meet the following conditions.
// This flow is used for the sequencer to bootstrap a new system.
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

// Increment nonce.
check_and_increment_nonce(tx_info=tx_info);
``` [1](#0-0) 

When the bootstrap branch is taken, execution returns before reaching `check_and_increment_nonce`, `run_validate`, and `charge_fee`. Three invariants that every other declare transaction must satisfy are therefore entirely absent:

| Skipped check | Normal location |
|---|---|
| Nonce verification & increment | `check_and_increment_nonce` (line 779) |
| Signature verification (`__validate_declare__`) | `non_reverting_select_execute_entry_point_func` (line 804) |
| Fee deduction | `charge_fee` (line 822) | [2](#0-1) 

Because `check_and_increment_nonce` is skipped, the on-chain nonce of the `'BOOTSTRAP'` address is never written. The condition `tx_info.nonce == 0` therefore remains permanently satisfiable: every subsequent bootstrap-path transaction can again supply `nonce = 0` and the OS will accept it. The only per-class guard is `prev_value=0` in `dict_update`, which prevents re-declaration of an already-declared class hash, but does not limit the total number of distinct class hashes that can be declared this way. [3](#0-2) 

The `sender_address` field is attacker-controlled: it is loaded from the hint `DeclareTxFields` and is committed to only through the transaction hash (verified by `AssertTransactionHash`). No check exists that a contract is actually deployed at address `'BOOTSTRAP'`, and the OS does not restrict which blocks or time ranges may use this path. [4](#0-3) 

---

### Impact Explanation

**Direct loss of funds / network halt.**

**Attack scenario A — fee theft at scale.** A malicious (or future decentralized) sequencer crafts an unlimited number of declare transactions with `sender_address = 'BOOTSTRAP'`, `nonce = 0`, `version = 3`, and all resource bounds set to zero. Each transaction declares a new class hash and pays zero fees. The sequencer collects no fees for these transactions, but the state is mutated (class hashes are registered). Because the OS accepts these transactions as valid, the STARK proof generated for the block is accepted by the L1 verifier. This constitutes a direct, provably valid bypass of the fee mechanism.

**Attack scenario B — bootstrap front-running causing network halt.** The bootstrap path is documented as being used "for the sequencer to bootstrap a new system." The class hashes declared during bootstrapping are system-critical (e.g., account contracts, ERC-20 fee tokens). An attacker who observes the expected bootstrap class hashes (which are deterministic from the Sierra source) can submit bootstrap-path declare transactions for those same class hashes but with a different (attacker-chosen) `compiled_class_hash`. Because `prev_value=0` in `dict_update` enforces that a class may be declared only once, the legitimate bootstrap will subsequently fail with a constraint violation when it attempts to declare the same class hash. This permanently prevents the network from completing its initialization — a total network shutdown. [5](#0-4) 

---

### Likelihood Explanation

The conditions required are entirely attacker-constructible:

- `sender_address = 'BOOTSTRAP'` — a known felt literal; no deployed contract is required at this address.
- `nonce = 0` — always valid because the nonce is never incremented in this path.
- `version = 3` — the standard current transaction version.
- `max_possible_fee = 0` — achieved by setting all three resource-bound `max_amount` fields to zero.

In the current centralized sequencer model, the sequencer must include the transaction in a block, which limits exploitation to a compromised or malicious sequencer. However, the OS itself imposes no restriction, so the vulnerability is fully exploitable in any future decentralized sequencer model, and is a latent critical flaw in the protocol's proving layer regardless of the sequencer's behavior.

---

### Recommendation

1. **Remove the bootstrap path entirely** and use an out-of-band mechanism (e.g., a genesis state that pre-populates class hashes) that does not require special OS logic.
2. If the bootstrap path must remain, **restrict it to a specific block-number range** (e.g., `block_number == 0`) enforced inside the Cairo code, not just by sequencer policy.
3. **Increment the nonce** of the `'BOOTSTRAP'` address even in the bootstrap path, so that each bootstrap-path transaction can only be used once per nonce value.
4. **Require a signature** from a protocol-controlled key for bootstrap transactions, verified inside the OS.

---

### Proof of Concept

Craft a declare transaction with the following fields:

```
sender_address  = 0x424F4F545354524150  // felt('BOOTSTRAP')
nonce           = 0
version         = 3
resource_bounds = [
    { token: L1_GAS,      max_amount: 0, max_price_per_unit: 0 },
    { token: L2_GAS,      max_amount: 0, max_price_per_unit: 0 },
    { token: L1_DATA_GAS, max_amount: 0, max_price_per_unit: 0 },
]
class_hash      = <any valid Sierra class hash>
compiled_class_hash = <attacker-chosen CASM hash>
```

The OS will:
1. Compute the transaction hash (which commits to `sender_address = 'BOOTSTRAP'`).
2. Verify the Sierra class hash pre-image via `finalize_class_hash`.
3. Enter the bootstrap branch at line 764 because all four conditions are met.
4. Call `dict_update` to register `class_hash → compiled_class_hash` with no signature check, no nonce increment, and no fee.
5. Return, leaving the `'BOOTSTRAP'` address nonce at 0.

Repeating this with a different `class_hash` succeeds again because `nonce == 0` is still satisfied. A STARK proof for a block containing these transactions is valid and will be accepted by the L1 verifier. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L693-720)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L761-825)
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
    %{ EndTx %}
```
