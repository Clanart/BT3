[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** third_party/move/move-bytecode-verifier/src/code_unit_verifier.rs (L48-52)
```rust
    fn verify_module_impl(
        verifier_config: &VerifierConfig,
        module: &'a CompiledModule,
    ) -> PartialVMResult<()> {
        let mut meter = BoundMeter::new(verifier_config);
```

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

**File:** third_party/move/move-bytecode-verifier/src/meter.rs (L25-27)
```rust
    /// Transfer the amount of metering from once scope to the next. If the current scope has
    /// metered N units, the target scope will be charged with N*factor.
    fn transfer(&mut self, from: Scope, to: Scope, factor: f32) -> PartialVMResult<()>;
```

**File:** third_party/move/move-bytecode-verifier/src/meter.rs (L111-125)
```rust
impl BoundMeter {
    pub fn new(config: &VerifierConfig) -> Self {
        Self {
            mod_bounds: Bounds {
                name: "<unknown>".to_string(),
                units: 0,
                max: config.max_per_mod_meter_units,
            },
            fun_bounds: Bounds {
                name: "<unknown>".to_string(),
                units: 0,
                max: config.max_per_fun_meter_units,
            },
        }
    }
```
