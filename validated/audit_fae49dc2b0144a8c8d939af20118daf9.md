### Title
Unauthenticated Bootstrap Path in `execute_declare_transaction` Bypasses Signature Verification — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The StarkNet OS contains a special-case "bootstrap" code path inside `execute_declare_transaction` that completely skips signature validation, nonce enforcement, and fee charging when a declare transaction's `sender_address` equals the felt literal `'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_fee == 0`. The guard is a plain felt comparison with no cryptographic backing. Any unprivileged actor who can get such a transaction included in a block can declare arbitrary class hashes on-chain without owning or controlling any account contract, directly analogous to the VoterProxy `deposit()` accepting arbitrary `_token`/`_gauge` from any caller.

---

### Finding Description

Inside `execute_declare_transaction`, after computing the transaction hash, the OS checks:

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

When all four conditions are satisfied the OS:

1. **Skips `check_and_increment_nonce`** — no nonce replay protection.
2. **Skips `run_validate`** — the account contract's `__validate_declare__` entry point is never called, so no signature is verified.
3. **Skips `charge_fee`** — the transaction is free.
4. Writes `class_hash → compiled_class_hash` directly into `contract_class_changes`.

The only guard is `sender_address == 'BOOTSTRAP'`. In Cairo, `'BOOTSTRAP'` is the felt encoding of the ASCII string "BOOTSTRAP" — a fixed public constant. There is no cryptographic proof that the submitter controls any privileged key or account. Any party who can craft a declare transaction with that felt value as the sender address satisfies the check.

The normal declare flow that is bypassed is:

```cairo
// Increment nonce.
check_and_increment_nonce(tx_info=tx_info);
...
// Run the account contract's "__validate_declare__" entry point.
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=validate_declare_execution_context
);
``` [2](#0-1) 

The `run_validate` function that enforces signature verification is: [3](#0-2) 

None of this runs in the bootstrap path.

---

### Impact Explanation

**Critical — Direct loss of funds.**

An attacker who successfully triggers the bootstrap path can declare an arbitrary `(class_hash, compiled_class_hash)` pair without owning any account. Because `prev_value=0` is enforced, they cannot overwrite an existing class, but they can register a brand-new class hash that maps to a compiled class of their choosing. If the sequencer's compiled-class database contains (or can be made to contain) the corresponding CASM for a malicious contract, the attacker can subsequently deploy contracts under that class hash. Users who interact with those contracts — e.g., by depositing tokens — lose funds permanently, because the malicious contract can drain balances or block withdrawals.

The `validate_compiled_class_facts_post_execution` call at the end of block execution validates only compiled class facts that were *executed* in that block. [4](#0-3) 

A bootstrap declare that is not executed in the same block escapes this check, leaving an unvalidated entry in the state.

---

### Likelihood Explanation

**Medium.**

The attacker must get a transaction with `sender_address = 'BOOTSTRAP'`, `nonce = 0`, `version = 3`, and zero resource bounds included in a sequenced block. In the current deployment the sequencer performs off-chain mempool validation that would normally reject such a transaction. However:

- The OS itself imposes **no cryptographic barrier** — the felt comparison is the only guard.
- A sequencer operator (or a future alternative sequencer) that does not replicate this off-chain check would pass the transaction straight through.
- The OS proof would be valid, the L1 verifier would accept the block, and the class would be permanently registered on-chain.

Because the OS is the authoritative verifier whose output is trusted by L1, any gap in OS-level enforcement is a protocol-level vulnerability regardless of sequencer-side mitigations.

---

### Recommendation

1. **Remove the felt-literal guard entirely.** The bootstrap mechanism should not rely on a publicly known magic address value. If bootstrapping is genuinely required, it should be gated by a verifiable condition — for example, a block number range, a governance-controlled flag committed in the OS config hash, or a threshold signature from the known public keys already present in `OsGlobalContext`.

2. **If the bootstrap path must remain**, require a valid signature from one of the `public_keys` already authenticated in `get_os_global_context` / `get_public_keys_hash`, so that the bypass is cryptographically tied to a known trusted key set. [5](#0-4) 

3. **Add an OS-level assertion** that `sender_address` cannot equal the felt value of `'BOOTSTRAP'` for any transaction that goes through the normal validation path, so that the two paths are mutually exclusive and auditable.

---

### Proof of Concept

1. Attacker computes the felt value `F = int.from_bytes(b'BOOTSTRAP', 'big')`.
2. Attacker selects a target `class_hash` (not yet declared) and a `compiled_class_hash` corresponding to a malicious CASM contract.
3. Attacker crafts a v3 declare transaction:
   - `sender_address = F`
   - `nonce = 0`
   - `version = 3`
   - All resource bounds set to `(max_amount=0, max_price_per_unit=0)` → `max_possible_fee = 0`
   - `class_hash` = target class hash
   - `compiled_class_hash` = hash of malicious CASM
4. Transaction is submitted (or directly sequenced). The OS evaluates:
   ```
   sender_address == 'BOOTSTRAP'  → TRUE
   tx_info.nonce == 0             → TRUE
   tx_info.version == 3           → TRUE
   max_possible_fee == 0          → TRUE
   ```
5. OS executes the bootstrap branch: writes `class_hash → compiled_class_hash` into `contract_class_changes`, returns without calling `__validate_declare__` or incrementing any nonce.
6. State update commits the new class. Attacker deploys a contract under `class_hash`. Users who interact with it lose funds. [1](#0-0)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L779-812)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L116-158)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L89-96)
```text
    local public_keys: felt*;
    local n_public_keys: felt;
    %{ GetPublicKeys %}

    // Build OS global context.
    let os_global_context = get_os_global_context(
        n_public_keys=n_public_keys, public_keys=public_keys
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L116-120)
```text
    validate_compiled_class_facts_post_execution(
        n_compiled_class_facts=compiled_class_facts_bundle.n_compiled_class_facts,
        compiled_class_facts=compiled_class_facts_bundle.compiled_class_facts,
        builtin_costs=compiled_class_facts_bundle.builtin_costs,
    );
```
