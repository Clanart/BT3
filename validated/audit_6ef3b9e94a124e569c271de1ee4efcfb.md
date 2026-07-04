### Title
Unchecked Return Value of Fee Token `transfer` Call in `charge_fee` - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in `transaction_impls.cairo` invokes the fee token's `transfer` entry point via `non_reverting_select_execute_entry_point_func` but **completely discards the return data**. If the fee token's `transfer` implementation returns a failure indicator (`false`) without reverting — a valid ERC20/ERC20-like behavior — the OS silently proceeds as if the fee was successfully collected, while the sequencer receives nothing.

---

### Finding Description

`charge_fee` constructs an `ExecutionContext` targeting the fee token contract with `TRANSFER_ENTRY_POINT_SELECTOR` and calls:

```cairo
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [1](#0-0) 

The function signature of `non_reverting_select_execute_entry_point_func` explicitly returns `(retdata_size: felt, retdata: felt*, is_deprecated: felt)`: [2](#0-1) 

The return tuple — which for an ERC20-style `transfer` would include a boolean success value — is never captured or inspected in `charge_fee`.

**Contrast with every other call site** in the same file, where the return value is always captured and the `VALIDATED` magic value is asserted:

- `run_validate`: `let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(...)` → `assert retdata[0] = VALIDATED` [3](#0-2) 

- `execute_deploy_account_transaction`: same pattern, return value captured and checked. [4](#0-3) 

- `execute_declare_transaction`: same pattern, return value captured and checked. [5](#0-4) 

`non_reverting_select_execute_entry_point_func` only asserts `is_reverted = 0` — it does **not** inspect `retdata`: [6](#0-5) 

So a fee token whose `transfer` returns `[0]` (false) without reverting passes through `charge_fee` undetected.

---

### Impact Explanation

**Critical — Direct loss of funds.**

The sequencer's fee revenue depends entirely on `charge_fee` correctly verifying that the ERC20 `transfer` to `sequencer_address` succeeded. If the transfer silently fails (returns `false`), the OS state transition is committed — the transaction is finalized, the user's nonce is incremented, and execution effects are applied — but the sequencer receives zero fee tokens. Over many transactions this constitutes a direct, permanent loss of sequencer funds with no recovery path inside the OS.

---

### Likelihood Explanation

The fee token address is protocol-configured (`block_context.os_global_context.starknet_os_config.fee_token_address`), so an attacker cannot substitute an arbitrary token. [7](#0-6) 

However:
1. Any future upgrade or misconfiguration of the fee token contract that introduces a non-reverting failure path immediately makes this exploitable by **any unprivileged transaction sender** — no special role required.
2. The OS itself provides no defense-in-depth; the missing check is a latent protocol-level flaw that is one fee-token upgrade away from being actively exploitable.

---

### Recommendation

Capture and validate the return data of the fee token `transfer` call inside `charge_fee`, mirroring the pattern used for all validate entry points:

```cairo
let (retdata_size, retdata, is_deprecated) =
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
// ERC20 transfer must return true (non-deprecated tokens).
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = 1;  // true = success
}
```

---

### Proof of Concept

1. Fee token contract is upgraded so that `transfer` returns `[0]` (false) instead of reverting when the sender has insufficient balance (or under any attacker-controlled condition).
2. Attacker submits any V3 invoke/declare/deploy-account transaction with non-zero resource bounds.
3. OS calls `charge_fee` → constructs `ExecutionContext` with `TRANSFER_ENTRY_POINT_SELECTOR` → calls `non_reverting_select_execute_entry_point_func`.
4. Fee token `transfer` executes, returns `retdata = [0]`, does not revert → `is_reverted = 0` assertion passes.
5. `charge_fee` returns without inspecting `retdata`.
6. OS commits the full state transition (nonce incremented, execution effects applied, block output written) with zero fee paid to the sequencer.
7. Sequencer suffers direct loss of fee revenue for every such transaction.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L138-138)
```text
    local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L160-164)
```text
    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L677-684)
```text
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_deploy_execution_context
        );
    }
    if (is_deprecated == 0) {
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L804-812)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L149-156)
```text
    let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
        block_context=block_context, execution_context=validate_execution_context
    );
    if (is_deprecated == 0) {
        %{ CheckRetdataForDebug %}
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L188-196)
```text
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    retdata_size: felt, retdata: felt*, is_deprecated: felt
) {
    let revert_log = init_revert_log();
    let (is_reverted, retdata_size, retdata, is_deprecated) = select_execute_entry_point_func{
        revert_log=revert_log
    }(block_context=block_context, execution_context=execution_context);
    assert is_reverted = 0;
    return (retdata_size, retdata, is_deprecated);
```
