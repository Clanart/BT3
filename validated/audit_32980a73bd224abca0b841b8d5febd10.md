[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** third_party/move/move-bytecode-verifier/src/control_flow.rs (L6-15)
```rust
//! This module implements control flow checks.
//!
//! For bytecode versions 6 and up, the following properties are ensured:
//! - The CFG is not empty and the last block ends in an unconditional jump, so it's not possible to
//!   fall off the end of a function.
//! - The CFG is reducible (and optionally max loop depth is bounded), to limit the potential for
//!   pathologically long abstract interpretation runtimes (through poor choice of loop heads and
//!   back edges).
//!
//! For bytecode versions 5 and below, delegates to `control_flow_v5`.
```

**File:** third_party/move/move-bytecode-verifier/src/control_flow.rs (L36-55)
```rust
pub fn verify_function<'a>(
    verifier_config: &'a VerifierConfig,
    module: &'a CompiledModule,
    index: FunctionDefinitionIndex,
    function_definition: &'a FunctionDefinition,
    code: &'a CodeUnit,
    _meter: &mut impl Meter, // TODO: metering
) -> PartialVMResult<FunctionView<'a>> {
    let function_handle = module.function_handle_at(function_definition.function);

    if module.version() <= 5 {
        control_flow_v5::verify(verifier_config, Some(index), code)?;
        Ok(FunctionView::function(module, index, code, function_handle))
    } else {
        verify_fallthrough(Some(index), code)?;
        let function_view = FunctionView::function(module, index, code, function_handle);
        verify_reducibility(verifier_config, &function_view)?;
        Ok(function_view)
    }
}
```

**File:** third_party/move/move-bytecode-verifier/src/control_flow.rs (L59-72)
```rust
pub fn verify_script<'a>(
    verifier_config: &'a VerifierConfig,
    script: &'a CompiledScript,
) -> PartialVMResult<FunctionView<'a>> {
    if script.version() <= 5 {
        control_flow_v5::verify(verifier_config, None, &script.code)?;
        Ok(FunctionView::script(script))
    } else {
        verify_fallthrough(None, &script.code)?;
        let function_view = FunctionView::script(script);
        verify_reducibility(verifier_config, &function_view)?;
        Ok(function_view)
    }
}
```

**File:** third_party/move/move-bytecode-verifier/bytecode-verifier-tests/src/unit_tests/control_flow_tests.rs (L54-64)
```rust
#[test]
fn empty_bytecode_v5() {
    let mut module = dummy_procedure_module(vec![]);
    module.version = 5;

    let result = verify_module(&Default::default(), &module);
    assert_eq!(
        result.unwrap_err().major_status(),
        StatusCode::EMPTY_CODE_UNIT,
    );
}
```

**File:** third_party/move/move-bytecode-verifier/src/script_signature.rs (L37-74)
```rust
pub fn verify_script(
    script: &CompiledScript,
    check_signature: FnCheckScriptSignature,
) -> VMResult<()> {
    if script.version >= VERSION_5 {
        return Ok(());
    }

    let resolver = &BinaryIndexedView::Script(script);
    let parameters = script.parameters;
    let return_ = None;
    verify_main_signature_impl(resolver, true, parameters, return_, check_signature)
        .map_err(|e| e.finish(Location::Script))
}

pub fn verify_module(
    module: &CompiledModule,
    check_signature: FnCheckScriptSignature,
) -> VMResult<()> {
    // important for not breaking old modules
    if module.version < VERSION_5 {
        return Ok(());
    }

    for (idx, _fdef) in module
        .function_defs()
        .iter()
        .enumerate()
        .filter(|(_idx, fdef)| fdef.is_entry)
    {
        verify_module_function_signature(
            module,
            FunctionDefinitionIndex(idx as TableIndex),
            check_signature,
        )?
    }
    Ok(())
}
```

**File:** third_party/move/move-bytecode-verifier/src/code_unit_verifier.rs (L111-147)
```rust
    fn verify_script_impl(
        verifier_config: &VerifierConfig,
        script: &'a CompiledScript,
    ) -> PartialVMResult<()> {
        let mut meter = BoundMeter::new(verifier_config);
        // create `FunctionView` and `BinaryIndexedView`
        let function_view = control_flow::verify_script(verifier_config, script)?;
        let resolver = BinaryIndexedView::Script(script);
        let name_def_map = HashMap::new();

        if let Some(limit) = verifier_config.max_basic_blocks_in_script {
            if function_view.cfg().blocks().len() > limit {
                return Err(PartialVMError::new(StatusCode::TOO_MANY_BASIC_BLOCKS));
            }
        }

        if let Some(limit) = verifier_config.max_back_edges_per_function {
            if function_view.cfg().num_back_edges() > limit {
                return Err(PartialVMError::new(StatusCode::TOO_MANY_BACK_EDGES));
            }
        }

        // INVARIANT: `struct_api_checker` is NOT called here for scripts, unlike for modules
        // (see `verify_module_impl`). This is safe because `CompiledScript` has no mechanism
        // to attach `FunctionAttribute`s to its main function: there is no `attributes` field
        // on the main function's representation (it is stored as a bare `CodeUnit`, not as a
        // `FunctionDefinition` with attributes). Therefore a script can never carry struct API
        // attributes such as `BorrowFieldMutable`, and `reference_safety` (called inside
        // `verify_common`) has no attributes to trust or mistrust.
        meter.enter_scope("script", Scope::Function);
        let code_unit_verifier = CodeUnitVerifier {
            resolver,
            function_view,
            name_def_map: &name_def_map,
        };
        code_unit_verifier.verify_common(verifier_config, &mut meter)
    }
```
