## Finding: On-chain view-function metadata verification does not check parameter types, only return type

### Summary
The on-chain publish-time verifier for `#[view]` metadata, `is_valid_view_function`, only checks that a claimed view function has a non-empty return signature. It does **not** check that the function has no `signer`/`&signer`/`&mut` parameters, unlike the Move compiler's `extended_checks.rs`, which enforces these rules only at compile time before bytecode ever reaches the chain.

### Finding Description
The on-chain publish path calls `verify_module_metadata_for_module_publishing`, which for every function tagged with the `ViewFunction`/`LegacyViewFunction` attribute in the module's Aptos metadata section invokes `is_valid_view_function`: [1](#0-0) 

This function only asserts `!sig.0.is_empty()` (a non-empty return signature) and performs no check on parameter types (no rejection of `signer`, `&signer`, or `&mut` parameters), and this is the only check exercised in the actual publish flow: [2](#0-1) 

The stricter rule — that `#[view]` functions cannot take a `signer`/`&signer` parameter and must return values — exists only in the Move compiler's `ExtendedChecker`, which runs during package compilation/build tooling, not as part of the on-chain publish verification: [3](#0-2) 

The existing tests (`test_view_attribute_with_signer`, `test_view_attribute_with_ref_signer`) confirm rejection only happens through the compile-time `PackageBuilder`/`extended_checks` path, not the runtime `module_metadata.rs` verifier: [4](#0-3) 

Since `extended_checks.rs` is invoked by the official Move package-build tooling (client/CLI-side) rather than by `aptos_vm.rs`'s module publishing path, any bytecode + metadata that is assembled or hand-modified without going through that build step — e.g., a custom compiler, bytecode assembler, or post-compilation metadata patch — bypasses this stricter check entirely. The only gate remaining on-chain is `is_valid_view_function`, which does not inspect `func_handle.parameters` at all.

### Impact Explanation
An attacker can publish a module (or upgrade one they control) whose metadata marks a function as `#[view]` even though its `FunctionHandle` parameters include `&mut Signer`, `signer`, or other side-effect-implying types, as long as the function's return signature is non-empty. This desyncs the on-chain-stored/verified metadata from actual function semantics: any downstream consumer (indexer, explorer, wallet, third-party API) that trusts the `ViewFunction` attribute in module metadata as a promise of "read-only, safe to call without a real signer/side effects" is misled by attacker-controlled metadata that the on-chain verifier does not actually enforce.

Note: this does not let the attacker execute unintended writes through the official `/view` REST endpoint, since `execute_view_function`'s runtime argument construction will still fail to supply a real `signer` for such a call; the desync is purely between the on-chain metadata guarantee and what code the verifier actually accepts, not in the argument-construction/entry-function path (which has its own independent checks in `validate_combine_signer_and_txn_args`). [5](#0-4) 

### Likelihood Explanation
Moderate. Exploitation requires bypassing the standard Move compiler package-build flow (which normally enforces `extended_checks`) — i.e., publishing raw/hand-crafted or third-party-compiled bytecode with a forged Aptos metadata section. This is plausible since publish transactions accept raw module bytes and metadata bytes directly; nothing in `aptos_vm.rs`'s publish path re-runs `extended_checks`.

### Recommendation
Extend `is_valid_view_function` in `types/src/vm/module_metadata.rs` to also validate the function's parameter types against the same rules enforced by `extended_checks.rs::check_and_record_view_functions` (reject `signer`/`&signer` parameters, and optionally flag `&mut` parameters), so the on-chain verifier and the compiler-side extended checks agree regardless of how the bytecode/metadata was produced.

### Proof of Concept
A Rust unit test in `types/src/vm/module_metadata.rs` building a `CompiledModule` with a function whose `FunctionHandle::parameters` includes a `&mut Signer` (or plain `Signer`) type and a non-empty return signature, tagging it with `KnownAttribute::view_function()`, and asserting `verify_module_metadata_for_module_publishing`/`is_valid_view_function` returns `Ok(())` — demonstrating that the on-chain metadata verifier accepts a function shape that `extended_checks.rs` (and the existing `#[should_panic]` tests in `aptos-move/e2e-move-tests/src/tests/attributes.rs`) would reject when going through the standard compiler.

### Citations

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

**File:** types/src/vm/module_metadata.rs (L468-482)
```rust
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

**File:** aptos-move/e2e-move-tests/src/tests/attributes.rs (L37-76)
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

#[test]
#[should_panic]
fn test_view_attribute_with_ref_signer() {
    let mut h = MoveHarness::new();
    let account = h.new_account_at(AccountAddress::from_hex_literal("0xf00d").unwrap());

    let mut builder = PackageBuilder::new("Package");
    builder.add_source(
        "m.move",
        r#"
        module 0xf00d::M {
            #[view]
            fun view(_:&signer,value: u64): u64 { value }
        }
        "#,
    );
    let path = builder.write_to_temp().unwrap();
    h.create_publish_package(&account, path.path(), None, |_| {});
}

```

**File:** aptos-move/aptos-vm/src/verifier/transaction_arg_validation.rs (L153-166)
```rust
    // Entry function should not return.
    if !func.return_tys().is_empty() {
        return Err(VMStatus::error(
            StatusCode::INVALID_MAIN_FUNCTION_SIGNATURE,
            None,
        ));
    }
    let mut signer_param_cnt = 0;
    // find all signer params at the beginning
    for ty in func.param_tys() {
        if ty.is_signer_or_signer_ref() {
            signer_param_cnt += 1;
        }
    }
```
