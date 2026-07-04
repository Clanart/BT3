### Title
Unauthorized Class Declaration via Unauthenticated Bootstrap Path — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_declare_transaction` contains a hardcoded bootstrap path that completely skips signature verification (`run_validate`) and nonce enforcement (`check_and_increment_nonce`) whenever a declare transaction carries `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_fee == 0`. Because `'BOOTSTRAP'` is a publicly known felt literal and all four conditions are freely settable by any transaction submitter, an unprivileged actor can declare an arbitrary `(class_hash, compiled_class_hash)` pair into the OS state without owning or controlling any account, bypassing the only authorization gate that protects the class registry.

---

### Finding Description

Inside `execute_declare_transaction`, after the transaction hash is computed, the following branch is evaluated: [1](#0-0) 

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
```

When this branch is taken the function returns **before** reaching:

- `check_and_increment_nonce` (line 779) — nonce replay protection [2](#0-1) 
- `run_validate` (line 804) — the account's `__validate_declare__` entry point, which is the sole signature-verification step [3](#0-2) 

The `%{ AssertTransactionHash %}` call is a **hint**, not a Cairo constraint; it does not appear in the proof and imposes no soundness obligation. The only Cairo-level constraints that remain in the bootstrap path are:

1. `compiled_class_hash != 0`
2. `dict_update` with `prev_value = 0` (class not yet declared)

Neither constraint requires the submitter to own or control the `'BOOTSTRAP'` address. The felt literal `'BOOTSTRAP'` is the ASCII encoding `0x424f4f545354524150`—a publicly known constant visible in the source. All four trigger conditions (`sender_address`, `nonce`, `version`, zero resource bounds) are fields that any user freely sets when constructing a declare transaction.

`compute_max_possible_fee` returns zero whenever all `max_amount` fields in the three resource-bound slots are zero, which is a valid and submittable transaction configuration: [4](#0-3) 

---

### Impact Explanation

The class registry (`contract_class_changes` dict) maps `class_hash → compiled_class_hash`. Once a mapping is written with `prev_value = 0`, it is permanent for that class hash (re-declaration is blocked by the same `prev_value = 0` guard). An attacker who front-runs a legitimate bootstrap declare can:

1. **Poison a core class hash** — associate a malicious `compiled_class_hash` with a class hash the system intends to use for the fee token, account abstraction base, or any other privileged contract.
2. **Block legitimate bootstrap** — because `prev_value = 0` is enforced, the real bootstrap transaction will fail with a dict-update mismatch, permanently preventing the correct class from being registered.
3. **Cause direct loss of funds** — any contract subsequently deployed or upgraded to the poisoned class hash will execute the attacker-chosen CASM bytecode. If that contract holds or controls user funds (e.g., the fee token or a system vault), those funds are at the attacker's disposal.

This satisfies **Critical — Direct loss of funds** from the allowed impact scope.

---

### Likelihood Explanation

- The trigger conditions are entirely under the submitter's control and require no privileged key or role.
- `'BOOTSTRAP'` is a hardcoded public constant in the open-source OS; no reverse engineering is needed.
- The attack window is the bootstrap phase of any new StarkNet deployment or upgrade, a predictable and observable on-chain event.
- The sequencer may perform off-chain pre-filtering, but the OS is the cryptographic enforcement layer; a proof produced from a block containing this transaction is valid by the OS's own Cairo constraints, meaning the L1 verifier will accept it.
- Likelihood is **High** during bootstrap windows and **Low** otherwise (the `prev_value = 0` guard limits the attack to undeclared classes).

---

### Recommendation

1. **Remove the magic-address bootstrap path entirely.** Core system classes should be declared through a privileged, cryptographically authenticated mechanism (e.g., a multi-sig or a sequencer-signed proof-of-authority field verified in Cairo, not just in a hint).
2. If a zero-account bootstrap is operationally required, gate it with a Cairo-level assertion against a public key committed in the OS config (analogous to how `public_keys_hash` is already committed in `StarknetOsConfig`), so the proof itself enforces authorization.
3. At minimum, add a Cairo `assert` that the `sender_address` matches a value committed in the verifiable OS configuration, not a hardcoded felt literal.

---

### Proof of Concept

```
Attacker constructs a declare transaction T_attack:
  sender_address        = 0x424f4f545354524150  // felt('BOOTSTRAP')
  nonce                 = 0
  version               = 3
  tip                   = 0
  resource_bounds       = [{L1_GAS, max_amount=0, max_price=0},
                           {L2_GAS, max_amount=0, max_price=0},
                           {L1_DATA_GAS, max_amount=0, max_price=0}]
  class_hash            = <target class hash, e.g. fee token class>
  compiled_class_hash   = <hash of attacker-controlled CASM>
  signature             = []   // empty — never checked

Sequencer includes T_attack before the legitimate bootstrap transaction.

OS execute_declare_transaction:
  1. Loads sender_address = 'BOOTSTRAP', nonce = 0, version = 3.
  2. Computes transaction hash (hint only, no Cairo constraint on signature).
  3. fill_account_tx_info() fills TxInfo — no signature check here.
  4. Bootstrap condition: TRUE.
  5. compute_max_possible_fee() = 0: TRUE.
  6. assert_not_zero(compiled_class_hash): passes (attacker chose non-zero).
  7. dict_update(key=class_hash, prev_value=0, new_value=attacker_compiled_class_hash).
  8. Returns — run_validate() never called.

Legitimate bootstrap transaction T_legit arrives next:
  dict_update(key=class_hash, prev_value=0, ...) FAILS — prev_value is now
  attacker_compiled_class_hash, not 0. Bootstrap is permanently blocked.

Any contract deployed to class_hash now executes attacker CASM → funds drained.
``` [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L95-101)
```text
    tempvar l1_gas_bounds: ResourceBounds = resource_bounds[L1_GAS_INDEX];
    tempvar l2_gas_bounds: ResourceBounds = resource_bounds[L2_GAS_INDEX];
    tempvar l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];

    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L750-779)
```text
    fill_account_tx_info(
        transaction_hash=transaction_hash,
        common_tx_fields=common_tx_fields,
        account_deployment_data_size=account_deployment_data_size,
        account_deployment_data=account_deployment_data,
        proof_facts_size=0,
        proof_facts=cast(0, felt*),
        tx_info_dst=tx_info,
        deprecated_tx_info_dst=deprecated_tx_info,
    );

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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L803-806)
```text
        %{ StartTx %}
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_declare_execution_context
        );
```
