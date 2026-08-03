## Finding: On-chain view-function attribute validation omits the parameter-purity checks enforced only by the off-chain compiler, allowing a `signer`-parameter function to be published and tagged `#[view]`, and view-argument construction has a `Signer`-specific bypass that accepts caller-controlled signer bytes

### Title
Missing on-chain enforcement of `#[view]` signer-parameter restriction combined with `Signer`-argument passthrough in view execution allows unprivileged bytecode publishing to forge signer construction through the view-function API — (File: `types/src/vm/module_metadata.rs`, `aptos-move/aptos-vm/src/verifier/transaction_arg_validation.rs`)

### Summary
The compiler-level purity check that rejects `#[view]` functions with `signer`/`&signer` parameters lives only in `ExtendedChecker::check_and_record_view_functions` [1](#0-0)  — a lint that runs during local package building (`aptos-move/framework/src/built_package.rs`), not during on-chain publish verification. The actual on-chain gate, `is_valid_view_function`, only checks that the function's return signature is non-empty; it never inspects parameter types [2](#0-1) . An attacker who submits raw `CompiledModule` bytes plus hand-crafted metadata directly to `code_publish_package_txn` (bypassing the Move source compiler entirely) can tag a function taking a bare `signer` parameter with the `#[view]` `KnownAttribute`, and `verify_module_metadata_for_module_publishing` will accept it as long as the function returns a value [3](#0-2) .

### Finding Description
Once published, this module's function is treated as a genuine view function by `determine_is_view`, which only consults `fun_attributes` metadata with no additional signature checks [4](#0-3) , and is routed via the exact `api/types/src/view.rs::ViewFunction` resolution path into `execute_view_function_in_vm` / `validate_view_function` [5](#0-4) [6](#0-5) .

Critically, argument construction for view calls goes through `transaction_arg_validation::construct_arg`, which special-cases the `Signer` type: for `is_view == true` it simply returns the caller-supplied raw bytes as-is, whereas for ordinary (non-view) entry-function calls a raw `Signer` argument is always rejected: [7](#0-6) 

This means the only thing standing between an unprivileged, unauthenticated view-function API caller and constructing a `signer` value for an arbitrary address is: (1) the metadata saying the function is a "view" function, and (2) the function's declared parameter type being `Signer`. Neither of these is blocked on-chain — `is_valid_view_function` doesn't inspect parameters at all, so a function of the shape `fun evil(s: signer, ...): u64` can be legally tagged `#[view]` in metadata that is verified and stored on-chain.

The existing e2e tests (`test_view_attribute_with_signer`, `test_view_attribute_with_ref_signer`) only prove that the *official compiler path* rejects this pattern [8](#0-7) ; they do not exercise the raw-bytecode/metadata publish path, and `test_bad_view_attribute_in_compiled_module` demonstrates that raw-bytecode metadata tampering is a supported/tested attack surface for this exact validation boundary, though that specific test targets an unknown attribute kind, not the parameter-type gap [9](#0-8) .

### Impact Explanation
If the underlying Move VM interpreter constructs an actual `signer` capability value from the raw bytes handed to it for a `Signer`-typed parameter (the same mechanism used internally to materialize signers for genuine transaction senders), then an unprivileged attacker could obtain a forged `signer` for any address purely through the read-only, unauthenticated View Function API, without ever producing a valid transaction or private key. A function tagged `#[view]` that internally uses this forged signer to call `move_to`, `borrow_global_mut`, or other resource/capability-gated operations under an arbitrary address would let the attacker perform privileged state-dependent computations under identities they don't own — a direct violation of code-ownership/authorization guarantees that the publish/verification pipeline is supposed to preserve.

I was not able to fully trace, within the available context, the exact Move VM interpreter code path that deserializes a `Signer`-typed function argument from raw bytes into a native signer value (i.e., confirm whether `session.execute_loaded_function` actually treats the passthrough bytes as address bytes and mints an authorized signer, or whether some other invariant check blocks it). This is a load-bearing gap in the analysis and should be verified against `move-vm-runtime`'s argument/frame setup code before treating this as conclusively exploitable end-to-end.

### Likelihood Explanation
The publish-time gap is concretely reproducible with a one-line change to `is_valid_view_function`'s intended behavior versus its actual implementation: any attacker capable of assembling and submitting a `CompiledModule` (skipping the standard `aptos move compile`/CLI toolchain, which is a normal capability of any publisher) can produce metadata that passes `verify_module_metadata_for_module_publishing`. The `construct_arg` `Signer => if is_view { Ok(arg) }` branch is unconditional and requires no special permissions to trigger via the View Function API. The remaining uncertainty is purely about downstream signer materialization semantics in the interpreter, not about the reachability of the described publish/metadata/argument-construction paths, which are all confirmed in code.

### Recommendation
1. Extend `is_valid_view_function` in `types/src/vm/module_metadata.rs` to reject any `#[view]`-tagged function whose parameter list contains a `Signer` (by value) type, mirroring the compiler-level check in `extended_checks.rs`, so the restriction is enforced during actual on-chain publish verification rather than only in the optional off-chain lint.
2. Audit `transaction_arg_validation::construct_arg`'s `Signer => if is_view { Ok(arg) }` branch: confirm whether raw caller-supplied bytes can be turned into an authorized `signer` value by the interpreter, and if so, either remove this passthrough entirely for view calls or ensure the on-chain metadata validator makes such a parameter combination impossible to publish.
3. Add a unit/e2e test that publishes a module via raw bytecode/metadata construction (bypassing the compiler) with `#[view] fun f(s: signer): u64 { ... }` and asserts that `verify_module_metadata_for_module_publishing` / `AttributeValidationError` rejects it, per the proof idea in the question.

### Proof of Concept
1. Hand-craft a `CompiledModule` with a public function `fn f(s: signer): u64 { 0 }` (bypassing `aptos move compile`, analogous to the `build_package_and_insert_attribute` helper used in `test_bad_view_attribute_in_compiled_module` [9](#0-8) ).
2. Attach `RuntimeModuleMetadataV1` marking `f` with `KnownAttribute::view_function()`.
3. Submit via `aptos_stdlib::code_publish_package_txn(metadata, code)` — `verify_module_metadata_for_module_publishing` accepts it because `is_valid_view_function` only checks `sig.0.is_empty()` on the return type [2](#0-1) .
4. Call the module's function through the View Function API (`ViewFunction` resolution in `api/types/src/view.rs`), supplying arbitrary bytes for the `signer` argument; trace whether `construct_arg`'s `Signer => Ok(arg)` branch [7](#0-6)  results in the interpreter materializing an authorized signer for the attacker-chosen address.

### Citations

**File:** aptos-move/framework/src/extended_checks.rs (L753-794)
```rust
impl ExtendedChecker<'_> {
    fn check_and_record_view_functions(&mut self, module: &ModuleEnv) {
        for ref fun in module.get_functions() {
            if !self.has_attribute(fun, VIEW_FUN_ATTRIBUTE) {
                continue;
            }
            self.check_transaction_args(&fun.get_parameters());
            if fun.get_return_count() == 0 {
                self.env
                    .error(&fun.get_id_loc(), "`#[view]` function must return values")
            }

            fun.get_parameters()
                .iter()
                .for_each(
                    |Parameter(_sym, parameter_type, param_loc)| match parameter_type {
                        Type::Primitive(inner) => {
                            if inner == &PrimitiveType::Signer {
                                self.env.error(
                                    param_loc,
                                    "`#[view]` function cannot use a `signer` parameter",
                                )
                            }
                        },
                        Type::Reference(mutability, inner) => {
                            if let Type::Primitive(inner) = inner.as_ref() {
                                if inner == &PrimitiveType::Signer
                                // Avoid a redundant error message for `&mut signer`, which is
                                // always disallowed for transaction entries, not just for
                                // `#[view]`.
                                    && mutability == &ReferenceKind::Immutable
                                {
                                    self.env.error(
                                        param_loc,
                                        "`#[view]` function cannot use the `&signer` parameter",
                                    )
                                }
                            }
                        },
                        _ => (),
                    },
                );
```

**File:** types/src/vm/module_metadata.rs (L378-396)
```rust
pub fn is_valid_view_function(
    module: &CompiledModule,
    functions: &BTreeMap<&IdentStr, (&FunctionHandle, &FunctionDefinition)>,
    fun: &str,
) -> Result<(), AttributeValidationError> {
    if let Ok(ident_fun) = Identifier::new(fun) {
        if let Some((func_handle, _func_def)) = functions.get(ident_fun.as_ident_str()) {
            let sig = module.signature_at(func_handle.return_);
            if !sig.0.is_empty() {
                return Ok(());
            }
        }
    }

    Err(AttributeValidationError {
        key: fun.to_string(),
        attribute: KnownAttributeKind::ViewFunction as u8,
    })
}
```

**File:** types/src/vm/module_metadata.rs (L441-482)
```rust
pub fn verify_module_metadata_for_module_publishing(
    module: &CompiledModule,
    features: &Features,
) -> Result<(), MetaDataValidationError> {
    if features.is_enabled(FeatureFlag::SAFER_METADATA) {
        check_module_complexity(module)?;
    }

    if features.are_resource_groups_enabled() {
        check_metadata_format(module)?;
    }
    let metadata = if let Some(metadata) = get_metadata_from_compiled_code(module) {
        metadata
    } else {
        return Ok(());
    };

    let functions = module
        .function_defs
        .iter()
        .map(|func_def| {
            let func_handle = module.function_handle_at(func_def.function);
            let name = module.identifier_at(func_handle.name);
            (name, (func_handle, func_def))
        })
        .collect::<BTreeMap<_, _>>();

    for (fun, attrs) in &metadata.fun_attributes {
        for attr in attrs {
            if attr.is_view_function() {
                is_valid_view_function(module, &functions, fun)?;
            } else if attr.is_randomness() {
                is_valid_unbiasable_function(&functions, fun)?;
            } else {
                return Err(AttributeValidationError {
                    key: fun.clone(),
                    attribute: attr.kind,
                }
                .into());
            }
        }
    }
```

**File:** aptos-move/aptos-vm/src/verifier/view_function.rs (L19-31)
```rust
pub fn determine_is_view(
    module_metadata: Option<&RuntimeModuleMetadataV1>,
    fun_name: &IdentStr,
) -> bool {
    if let Some(data) = module_metadata {
        data.fun_attributes
            .get(fun_name.as_str())
            .map(|attrs| attrs.iter().any(|attr| attr.is_view_function()))
            .unwrap_or_default()
    } else {
        false
    }
}
```

**File:** aptos-move/aptos-vm/src/verifier/view_function.rs (L35-61)
```rust
pub(crate) fn validate_view_function(
    session: &mut SessionExt<impl AptosMoveResolver>,
    loader: &impl Loader,
    gas_meter: &mut impl GasMeter,
    traversal_context: &mut TraversalContext,
    args: Vec<Vec<u8>>,
    fun_name: &IdentStr,
    func: &LoadedFunction,
    module_metadata: Option<&RuntimeModuleMetadataV1>,
    struct_constructors_feature: bool,
) -> PartialVMResult<Vec<Vec<u8>>> {
    // Must be marked as view function.
    let is_view = determine_is_view(module_metadata, fun_name);
    if !is_view {
        return Err(
            PartialVMError::new(StatusCode::INVALID_MAIN_FUNCTION_SIGNATURE)
                .with_message("function not marked as view function".to_string()),
        );
    }

    // Must return values.
    if func.return_tys().is_empty() {
        return Err(
            PartialVMError::new(StatusCode::INVALID_MAIN_FUNCTION_SIGNATURE)
                .with_message("view function must return values".to_string()),
        );
    }
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L3039-3073)
```rust
    fn execute_view_function_in_vm(
        session: &mut SessionExt<impl AptosMoveResolver>,
        vm: &AptosVM,
        module_id: ModuleId,
        func_name: Identifier,
        ty_args: Vec<TypeTag>,
        arguments: Vec<Vec<u8>>,
        gas_meter: &mut impl AptosGasMeter,
        traversal_context: &mut TraversalContext,
        module_storage: &impl AptosModuleStorage,
    ) -> Result<Vec<Vec<u8>>, VMError> {
        dispatch_loader!(module_storage, loader, {
            let func = loader.load_instantiated_function(
                &LegacyLoaderConfig::unmetered(),
                gas_meter,
                traversal_context,
                &module_id,
                &func_name,
                &ty_args,
            )?;

            let metadata = get_metadata(&func.owner_as_module()?.metadata);

            let arguments = view_function::validate_view_function(
                session,
                &loader,
                gas_meter,
                traversal_context,
                arguments,
                func_name.as_ident_str(),
                &func,
                metadata.as_ref().map(Arc::as_ref),
                vm.features().is_enabled(FeatureFlag::STRUCT_CONSTRUCTORS),
            )
            .map_err(|e| e.finish(Location::Module(module_id)))?;
```

**File:** aptos-move/aptos-vm/src/verifier/transaction_arg_validation.rs (L565-571)
```rust
        Signer => {
            if is_view {
                Ok(arg)
            } else {
                Err(invalid_signature())
            }
        },
```

**File:** aptos-move/e2e-move-tests/src/tests/attributes.rs (L37-55)
```rust
#[test]
#[should_panic]
fn test_view_attribute_with_signer() {
    let mut h = MoveHarness::new();
    let account = h.new_account_at(AccountAddress::from_hex_literal("0xf00d").unwrap());

    let mut builder = PackageBuilder::new("Package");
    builder.add_source(
        "m.move",
        r#"
        module 0xf00d::M {
            #[view]
            fun view(_:signer,value: u64): u64 { value }
        }
        "#,
    );
    let path = builder.write_to_temp().unwrap();
    h.create_publish_package(&account, path.path(), None, |_| {});
}
```

**File:** aptos-move/e2e-move-tests/src/tests/attributes.rs (L198-219)
```rust
#[test]
fn test_bad_view_attribute_in_compiled_module() {
    let mut h = MoveHarness::new();
    let account = h.new_account_at(AccountAddress::from_hex_literal("0xf00d").unwrap());
    let source = r#"
        module 0xf00d::M {
            fun view(_value: u64) { }
        }
        "#;
    let fake_attribute = FakeKnownAttribute {
        kind: 1,
        args: vec![],
    };
    let (code, metadata) =
        build_package_and_insert_attribute(source, None, Some(("view", fake_attribute)));
    let result = h.run_transaction_payload(
        &account,
        aptos_stdlib::code_publish_package_txn(metadata, code),
    );

    assert_vm_status!(result, StatusCode::CONSTRAINT_NOT_SATISFIED);
}
```
