[1](#0-0) [2](#0-1)

### Citations

**File:** third_party/move/move-vm/runtime/src/native_models_for_runtime_ref_checks.rs (L44-79)
```rust
impl Default for NativeRuntimeRefChecksModel {
    /// Create default models for native functions that return references.
    fn default() -> Self {
        // First return value is a reference derived from the first reference parameter.
        // It is the only return value that is a reference.
        let single_return_derived_from_first_ref_param = vec![0];
        let models = BTreeMap::from([
            (
                ("storage_slot", "borrow_storage_slot_resource"),
                single_return_derived_from_first_ref_param.clone(),
            ),
            (
                ("storage_slot", "borrow_storage_slot_resource_mut"),
                single_return_derived_from_first_ref_param.clone(),
            ),
            (
                ("signer", "borrow_address"),
                single_return_derived_from_first_ref_param.clone(),
            ),
            (
                ("table", "borrow_box"),
                single_return_derived_from_first_ref_param.clone(),
            ),
            (
                ("table", "borrow_box_mut"),
                single_return_derived_from_first_ref_param,
            ),
        ]);
        let me = Self { models };
        debug_assert!(
            me.models.values().all(|m| Self::no_duplicates(m)),
            "duplicate derivations in a native model"
        );
        me
    }
}
```

**File:** third_party/move/move-vm/runtime/src/runtime_ref_checks.rs (L81-86)
```rust
//! Native functions implemented in Rust may also return references. Because such code
//! is a black box as far the Move VM instruction semantics are concerned, we need
//! their reference behavior to be modeled explicitly.
//! See the module `native_models_for_runtime_ref_checks` for documentation on how to
//! specify such models. These models are trusted but their reference safety is not
//! checked by runtime reference checks.
```
