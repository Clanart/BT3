[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** third_party/move/move-bytecode-verifier/src/code_unit_verifier.rs (L70-94)
```rust
        for (idx, function_definition) in module.function_defs().iter().enumerate() {
            let index = FunctionDefinitionIndex(idx as TableIndex);

            // SECURITY: Check struct API attributes BEFORE verify_function runs.
            // This ensures that reference_safety (which runs inside verify_function) can
            // safely trust BorrowFieldMutable attributes, since they've been validated
            // to accurately match the bytecode before reference_safety sees them.
            // Only runs for VERSION_10+ modules (see guard above).
            if let Some(ctx) = &struct_api_ctx {
                struct_api_checker::check_function(module, function_definition, ctx)
                    .map_err(|err| err.at_index(IndexKind::FunctionDefinition, index.0))?;
            }

            // Now reference_safety can safely trust that BorrowFieldMutable attributes
            // accurately describe which field is being borrowed
            let num_back_edges = Self::verify_function(
                verifier_config,
                index,
                function_definition,
                module,
                &name_def_map,
                &mut meter,
            )
            .map_err(|err| err.at_index(IndexKind::FunctionDefinition, index.0))?;
            total_back_edges += num_back_edges;
```

**File:** third_party/move/move-bytecode-verifier/src/struct_api_checker.rs (L22-24)
```rust
//! 1. Function name matches struct API pattern iff it has the corresponding struct API attribute
//! 2. Attribute type must match name pattern (e.g., pack$S requires Pack attribute)
//! 3. Only one struct API attribute allowed per function
```

**File:** third_party/move/move-bytecode-verifier/src/struct_api_checker.rs (L392-417)
```rust
    fn matches_attr(&self, attr: &FunctionAttribute) -> bool {
        match attr {
            FunctionAttribute::Pack => {
                self.prefix == NamePrefix::Pack && self.variant_name.is_none()
            },
            FunctionAttribute::PackVariant(_) => {
                self.prefix == NamePrefix::Pack && self.variant_name.is_some()
            },
            FunctionAttribute::Unpack => {
                self.prefix == NamePrefix::Unpack && self.variant_name.is_none()
            },
            FunctionAttribute::UnpackVariant(_) => {
                self.prefix == NamePrefix::Unpack && self.variant_name.is_some()
            },
            FunctionAttribute::TestVariant(_) => self.prefix == NamePrefix::TestVariant,
            FunctionAttribute::BorrowFieldImmutable(_) => {
                self.prefix == NamePrefix::Borrow && !self.is_mutable
            },
            FunctionAttribute::BorrowFieldMutable(_) => {
                self.prefix == NamePrefix::Borrow && self.is_mutable
            },
            // Non-struct-API attributes: these are filtered out by try_get_struct_api_attr
            // before matches_attr is ever called, so they can never be a match.
            FunctionAttribute::Persistent | FunctionAttribute::ModuleLock => false,
        }
    }
```

**File:** third_party/move/move-bytecode-verifier/src/struct_api_checker.rs (L454-463)
```rust
    let prefix_str = parts[0];

    let name_prefix = match prefix_str {
        PACK => NamePrefix::Pack,
        UNPACK => NamePrefix::Unpack,
        TEST_VARIANT => NamePrefix::TestVariant,
        BORROW | BORROW_MUT => NamePrefix::Borrow,
        _ => return None,
    };
    let is_mutable = prefix_str == BORROW_MUT;
```
