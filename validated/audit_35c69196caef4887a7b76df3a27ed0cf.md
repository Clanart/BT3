### Title
Unauthorized Class Declaration via Unauthenticated `BOOTSTRAP` Sender Bypass Skips Signature Validation — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

Inside `execute_declare_transaction`, a special branch keyed on `sender_address == 'BOOTSTRAP'` allows a class hash to be committed to the state with an **arbitrary `compiled_class_hash`**, bypassing signature validation, nonce enforcement, and fee charging entirely. Any actor who can get such a transaction included in a block — including a sequencer in a decentralized setting — can front-run a legitimate class declaration and permanently corrupt the class hash → compiled class hash mapping, freezing funds in any contract that depends on the targeted class.

---

### Finding Description

`execute_declare_transaction` in `transaction_impls.cairo` contains the following branch:

```cairo
// Lines 764–776
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
        return ();   // <-- early return, skipping ALL authorization
    }
}
``` [1](#0-0) 

When this branch is taken, the function returns immediately, skipping every subsequent security gate:

| Gate | Normal path | BOOTSTRAP path |
|---|---|---|
| `check_and_increment_nonce` | ✅ enforced | ❌ skipped |
| `run_validate` (signature check) | ✅ enforced | ❌ skipped |
| `charge_fee` | ✅ enforced | ❌ skipped | [2](#0-1) 

The conditions that gate this path are:

1. `sender_address == 'BOOTSTRAP'` — a plain felt literal (ASCII encoding `0x424f4f545354524150`), **not** a privileged key or role.
2. `nonce == 0`
3. `version == 3`
4. All resource bounds zero → `max_possible_fee == 0`

None of these conditions require the submitter to control any private key or privileged account. The `sender_address` field in a declare transaction is supplied by the transaction submitter via the hint `%{ DeclareTxFields %}`: [3](#0-2) 

The `compiled_class_hash` written into `contract_class_changes` is also attacker-controlled and is **not** validated against any actual compiled class in the BOOTSTRAP path. The only constraint is `assert_not_zero(compiled_class_hash)`. [4](#0-3) 

The `dict_update` call uses `prev_value=0`, which enforces that a class hash can only be declared once. This is the mechanism that makes the attack permanent: once a class hash is committed with a wrong `compiled_class_hash`, it cannot be overwritten. [5](#0-4) 

---

### Impact Explanation

**Permanent freezing of funds.**

The class hash → compiled class hash mapping is the authoritative record used by the OS to locate and execute contract code. If an attacker commits a bogus `compiled_class_hash` for a class that is used by contracts holding user funds (e.g., a wallet class, ERC-20 class, or any widely-deployed contract class), those contracts become permanently unexecutable:

- The OS will look up the compiled class by the stored (wrong) hash and find no matching compiled class.
- Every call into a contract of that class will fail.
- Funds held in those contracts are permanently frozen with no recovery path, because the `prev_value=0` constraint prevents re-declaration.

The attack is a **front-run**: the attacker observes a pending legitimate declare transaction (class hash is public), submits a BOOTSTRAP declare for the same class hash with a garbage `compiled_class_hash`, and if included first, the legitimate declaration fails with a dict constraint violation (`prev_value` is no longer 0).

---

### Likelihood Explanation

**Medium.**

- In the current centralized sequencer model, the sequencer operator controls which transactions are included. A malicious or compromised sequencer can include a BOOTSTRAP transaction without any user cooperation.
- In a decentralized sequencer model (which StarkNet is moving toward), any sequencer node can include such a transaction. The OS Cairo program is the source of truth for proof validity; if the OS accepts the transaction, the proof is valid and the L1 will accept it.
- The attacker does not need to control any private key, hold any funds, or have any on-chain presence. The only requirement is that the crafted transaction be included in a block.
- Class hashes of widely-used contracts are public and observable in the mempool, making front-running straightforward.

---

### Recommendation

1. **Remove the BOOTSTRAP path entirely**, or gate it behind a cryptographic check (e.g., require a valid signature from a well-known bootstrapper public key that is part of the OS configuration).
2. If the bootstrap mechanism is needed for system initialization, it should be a one-time flag checked against the OS configuration (e.g., `os_global_context.starknet_os_config`) rather than a magic felt literal in the sender address field.
3. At minimum, add a check that the BOOTSTRAP path can only be used when the state is empty (e.g., block number == 0 or a dedicated `is_bootstrap_block` flag), preventing its use in live blocks.

---

### Proof of Concept

**Precondition:** A legitimate developer has a pending declare transaction for class `C` with correct `compiled_class_hash = H_correct` in the mempool.

**Attack steps:**

1. Attacker observes the pending declare transaction and extracts `class_hash = C`.

2. Attacker crafts a declare transaction with:
   - `sender_address = 'BOOTSTRAP'` (felt `0x424f4f545354524150`)
   - `nonce = 0`
   - `version = 3`
   - `resource_bounds` all zero (so `max_possible_fee = 0`)
   - `class_hash = C` (same as the legitimate transaction)
   - `compiled_class_hash = 1` (any nonzero garbage value)

3. A sequencer (malicious, compromised, or the attacker themselves in a decentralized setting) includes the attacker's transaction before the legitimate one.

4. The OS processes the BOOTSTRAP transaction:
   - `sender_address == 'BOOTSTRAP'` ✅
   - `nonce == 0` ✅
   - `version == 3` ✅
   - `max_possible_fee == 0` ✅
   - Skips `run_validate`, `check_and_increment_nonce`, `charge_fee`
   - Calls `dict_update(key=C, prev_value=0, new_value=1)` — succeeds, commits `C → 1` to state.

5. The legitimate declare transaction is processed next:
   - `dict_update(key=C, prev_value=0, new_value=H_correct)` — **fails** with a constraint violation because `prev_value` is now `1`, not `0`.

6. Class `C` is now permanently declared with `compiled_class_hash = 1`. No compiled class with hash `1` exists. Every contract of class `C` is permanently unexecutable. All funds held in those contracts are frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L710-715)
```text
    local sender_address;
    local class_hash_ptr: felt*;
    local compiled_class_hash;
    local account_deployment_data_size;
    local account_deployment_data: felt*;
    %{ DeclareTxFields %}
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L778-826)
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
    %{ EndTx %}

```
