### Title
Sierra Contracts Missing `DEFAULT_ENTRY_POINT_SELECTOR` Fallback Causes Permanent Fund Freezing — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo`)

---

### Summary

The OS's `get_entry_point` function for Sierra (Cairo 1) contracts does not implement the `DEFAULT_ENTRY_POINT_SELECTOR` (0x0) fallback that exists for deprecated (CairoZero) contracts in `get_entry_point_offset`. When a Sierra contract defines a fallback entry point at selector 0x0 and is called with any non-matching selector, the OS returns `ERROR_ENTRY_POINT_NOT_FOUND` and reverts instead of routing to the fallback. This is a direct protocol-level analog to the missing `payable` on `fallback()`: a call that should succeed silently fails, and any funds locked in such a contract become permanently frozen.

---

### Finding Description

**Deprecated (CairoZero) path — `deprecated_execute_entry_point.cairo`:**

```cairo
// If the selector was not found, check if we have a default entry point.
if (n_entry_points != 0 and entry_points[0].selector == DEFAULT_ENTRY_POINT_SELECTOR) {
    return (success=1, entry_point_offset=entry_points[0].offset);
}
return (success=0, entry_point_offset=0);
``` [1](#0-0) 

When a selector is not found in a CairoZero contract, the OS checks whether the first entry point has `selector == DEFAULT_ENTRY_POINT_SELECTOR` (0x0) and, if so, routes the call there. This is the protocol's intended "fallback" mechanism.

**Sierra path — `execute_entry_point.cairo`:**

```cairo
let (entry_point_desc: CompiledClassEntryPoint*, success) = search_sorted_optimistic(
    array_ptr=cast(entry_points, felt*),
    elm_size=CompiledClassEntryPoint.SIZE,
    n_elms=n_entry_points,
    key=execution_context.execution_info.selector,
);
if (success != FALSE) {
    return (success=1, entry_point=entry_point_desc);
}

return (success=0, entry_point=cast(0, CompiledClassEntryPoint*));
``` [2](#0-1) 

There is **no fallback** to `DEFAULT_ENTRY_POINT_SELECTOR`. When the selector is not found, `success=0` is returned unconditionally.

The caller then handles `success == 0` as a hard revert:

```cairo
if (success == 0) {
    %{ ExitCall %}
    let (retdata: felt*) = alloc();
    assert retdata[0] = ERROR_ENTRY_POINT_NOT_FOUND;
    return (is_reverted=1, retdata_size=1, retdata=retdata);
}
``` [3](#0-2) 

The `DEFAULT_ENTRY_POINT_SELECTOR` constant is defined and used in the deprecated path but is never consulted in the Sierra path: [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A Sierra contract that:
1. Defines an entry point at selector `0x0` as a catch-all/fallback handler (e.g., a Sierra account or vault designed to accept arbitrary calls, mirroring the CairoZero pattern), and
2. Holds or custodies ERC-20 token balances or other assets,

will have every call with a non-matching selector silently reverted by the OS. Because the OS-level revert is unconditional and the contract's fallback entry point is never reached, any withdrawal, transfer, or recovery path that relies on the fallback is permanently blocked. Funds held by such a contract are irrecoverable without an OS upgrade — a permanent freeze.

---

### Likelihood Explanation

The `DEFAULT_ENTRY_POINT_SELECTOR` fallback is a documented, first-class protocol feature for CairoZero contracts. Sierra is the current and future standard for StarkNet contracts. Any Sierra contract author who ports a CairoZero contract that uses the fallback pattern, or who writes a Sierra contract expecting parity with the CairoZero protocol semantics (a reasonable expectation given the protocol documentation), will produce a contract whose fallback is silently dead. The entry path requires only a normal `call_contract` syscall from any unprivileged caller — no privileged access is needed. [5](#0-4) 

---

### Recommendation

Add the `DEFAULT_ENTRY_POINT_SELECTOR` fallback to `get_entry_point` in `execute_entry_point.cairo`, mirroring the logic already present in `get_entry_point_offset`:

```cairo
// After search_sorted_optimistic returns success=FALSE:
// If the selector was not found, check if we have a default entry point.
if (n_entry_points != 0 and entry_points[0].selector == DEFAULT_ENTRY_POINT_SELECTOR) {
    return (success=1, entry_point=entry_points);
}
return (success=0, entry_point=cast(0, CompiledClassEntryPoint*));
```

This restores protocol parity between CairoZero and Sierra contracts for the fallback entry point mechanism.

---

### Proof of Concept

1. Deploy a Sierra contract with a single external entry point at selector `DEFAULT_ENTRY_POINT_SELECTOR` (0x0), holding a token balance.
2. Issue a `call_contract` syscall targeting that contract with any selector other than 0x0 (e.g., a `transfer` selector).
3. **CairoZero equivalent**: `get_entry_point_offset` finds `entry_points[0].selector == 0x0`, routes to the fallback, call succeeds.
4. **Sierra**: `get_entry_point` finds no match, returns `success=0`, OS emits `ERROR_ENTRY_POINT_NOT_FOUND`, call is reverted.
5. The token balance in the Sierra contract is now unreachable via any selector other than exactly 0x0, and if the contract's withdrawal logic was intended to be triggered via the fallback, funds are permanently frozen. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_entry_point.cairo (L14-21)
```text
from starkware.starknet.core.os.constants import (
    DEFAULT_ENTRY_POINT_SELECTOR,
    ENTRY_POINT_TYPE_CONSTRUCTOR,
    ENTRY_POINT_TYPE_EXTERNAL,
    ENTRY_POINT_TYPE_L1_HANDLER,
    ERROR_ENTRY_POINT_NOT_FOUND,
    NOP_ENTRY_POINT_OFFSET,
)
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_entry_point.cairo (L33-77)
```text
func get_entry_point_offset{range_check_ptr}(
    compiled_class: DeprecatedCompiledClass*, execution_context: ExecutionContext*
) -> (success: felt, entry_point_offset: felt) {
    alloc_locals;
    // Get the entry points corresponding to the transaction's type.
    local entry_points: DeprecatedContractEntryPoint*;
    local n_entry_points: felt;

    tempvar entry_point_type = execution_context.entry_point_type;
    if (entry_point_type == ENTRY_POINT_TYPE_L1_HANDLER) {
        entry_points = compiled_class.l1_handlers;
        n_entry_points = compiled_class.n_l1_handlers;
    } else {
        if (entry_point_type == ENTRY_POINT_TYPE_EXTERNAL) {
            entry_points = compiled_class.external_functions;
            n_entry_points = compiled_class.n_external_functions;
        } else {
            assert entry_point_type = ENTRY_POINT_TYPE_CONSTRUCTOR;
            entry_points = compiled_class.constructors;
            n_entry_points = compiled_class.n_constructors;

            if (n_entry_points == 0) {
                return (success=1, entry_point_offset=NOP_ENTRY_POINT_OFFSET);
            }
        }
    }

    // The key must be at offset 0.
    static_assert DeprecatedContractEntryPoint.selector == 0;
    let (entry_point_desc: DeprecatedContractEntryPoint*, success) = search_sorted_optimistic(
        array_ptr=cast(entry_points, felt*),
        elm_size=DeprecatedContractEntryPoint.SIZE,
        n_elms=n_entry_points,
        key=execution_context.execution_info.selector,
    );
    if (success != FALSE) {
        return (success=1, entry_point_offset=entry_point_desc.offset);
    }

    // If the selector was not found, check if we have a default entry point.
    if (n_entry_points != 0 and entry_points[0].selector == DEFAULT_ENTRY_POINT_SELECTOR) {
        return (success=1, entry_point_offset=entry_points[0].offset);
    }
    return (success=0, entry_point_offset=0);
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L91-133)
```text
func get_entry_point{range_check_ptr}(
    compiled_class: CompiledClass*, execution_context: ExecutionContext*
) -> (success: felt, entry_point: CompiledClassEntryPoint*) {
    alloc_locals;
    // Get the entry points corresponding to the transaction's type.
    local entry_points: CompiledClassEntryPoint*;
    local n_entry_points: felt;

    tempvar entry_point_type = execution_context.entry_point_type;
    if (entry_point_type == ENTRY_POINT_TYPE_L1_HANDLER) {
        entry_points = compiled_class.l1_handlers;
        n_entry_points = compiled_class.n_l1_handlers;
    } else {
        if (entry_point_type == ENTRY_POINT_TYPE_EXTERNAL) {
            entry_points = compiled_class.external_functions;
            n_entry_points = compiled_class.n_external_functions;
        } else {
            assert entry_point_type = ENTRY_POINT_TYPE_CONSTRUCTOR;
            entry_points = compiled_class.constructors;
            n_entry_points = compiled_class.n_constructors;

            if (n_entry_points == 0) {
                return (success=1, entry_point=cast(0, CompiledClassEntryPoint*));
            }
        }
    }

    // The key must be at offset 0.
    static_assert CompiledClassEntryPoint.selector == 0;
    // TODO(Yoni, 1/1/2026): make sure the cost of searching missing keys is covered
    //   once reverted entrypoints are supported in the OS (should be fine).
    let (entry_point_desc: CompiledClassEntryPoint*, success) = search_sorted_optimistic(
        array_ptr=cast(entry_points, felt*),
        elm_size=CompiledClassEntryPoint.SIZE,
        n_elms=n_entry_points,
        key=execution_context.execution_info.selector,
    );
    if (success != FALSE) {
        return (success=1, entry_point=entry_point_desc);
    }

    return (success=0, entry_point=cast(0, CompiledClassEntryPoint*));
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L172-177)
```text
    if (success == 0) {
        %{ ExitCall %}
        let (retdata: felt*) = alloc();
        assert retdata[0] = ERROR_ENTRY_POINT_NOT_FOUND;
        return (is_reverted=1, retdata_size=1, retdata=retdata);
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L54-54)
```text
const DEFAULT_ENTRY_POINT_SELECTOR = 0x0;
```
