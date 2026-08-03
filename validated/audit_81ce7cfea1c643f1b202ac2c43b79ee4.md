## Analysis

`is_valid_view_function` in `types/src/vm/module_metadata.rs` only checks one thing: that the function's return signature is non-empty. [1](#0-0) 

It does **not** check the function's parameter types (e.g. rejecting `signer`/`&mut signer`), visibility, or whether the function body performs writes. This is enforced at publish time by `verify_module_metadata_for_module_publishing`, which is the on-chain gate that runs `is_valid_view_function`/`is_valid_unbiasable_function` against whatever `fun_attributes` metadata is embedded in the submitted module bytes: [2](#0-1) 

By contrast, the stricter signature constraints the finding refers to (rejecting `signer`/`&mut signer` parameters, requiring non-public entry for `#[view]`) are implemented only in the **Move Prover/compiler-level** `ExtendedChecker::check_and_record_view_functions`, which runs during `aptos move compile`/`aptos move publish` via the CLI/SDK build pipeline: [3](#0-2) 

This is a source-level lint that only fires when a module is built from Move source through the official compiler toolchain. It is not part of the bytecode verifier or the on-chain metadata validation, so a module submitted as raw bytecode via the standard publish transaction (`code::publish_package_txn`, permissionless for a user's own account/object) is checked only by `verify_module_metadata_for_module_publishing`, which does not re-derive `fun_attributes` from source — it simply validates the pre-baked metadata blob against the compiled bytecode using `is_valid_view_function`/`is_valid_unbiasable_function`.

Given `is_valid_view_function`'s only requirement is "non-empty return signature," a hand-crafted module (bypassing the CLI compiler and `ExtendedChecker`) containing a function that takes `&mut signer`, writes to global storage, and also returns a value would satisfy `is_valid_view_function` and be accepted on-chain with `fun_attributes` marking it `#[view]`. This means the **published, stored `RuntimeModuleMetadataV1`** can legitimately describe a state-mutating function as a view function — an actual metadata/behavior mismatch reachable through unprivileged publish, matching the "package metadata must describe the code that is actually verified and stored" pivot.

However, I could not fully verify the *execution-path* consequence within available context: I was unable to confirm from the indexed code whether the `/view` API's actual invocation path (`execute_view_function` in `aptos-move/aptos-vm/src/aptos_vm.rs` and `api/src/view_function.rs`) would ever succeed at actually running such a function — a `&mut signer` parameter has to be constructed by `transaction_arg_validation::construct_args`/`validate_view_function`, and I did not get to inspect the full body of `construct_args` in `transaction_arg_validation.rs` (only the constant/table definitions were retrieved) to confirm whether it rejects or silently mishandles a `signer` parameter type, nor did I retrieve the body of `execute_view_function` in `aptos_vm.rs`. This is an index/context limitation, not a claim that the path is safe — a Devin session with full repo access would be needed to trace `construct_args`'s handling of `Type::Signer`/`Type::Reference(_, Signer)` and confirm whether an actual on-chain `/view` call to such a function can succeed, or whether it fails at argument construction (in which case only the *stored metadata* is misleading, not live execution).

### Title
On-chain `#[view]` metadata validation (`is_valid_view_function`) does not reject `signer`/mutating function signatures, allowing mutating functions to be falsely marked as view - (File: types/src/vm/module_metadata.rs)

### Summary
`is_valid_view_function` (invoked from `verify_module_metadata_for_module_publishing`) is the sole on-chain gate for the `#[view]` `fun_attributes` entry, and it only checks that the target function has a non-empty return type. It does not check for `signer`/`&mut signer` parameters or otherwise verify the function is read-only, unlike the stricter `ExtendedChecker::check_and_record_view_functions` compiler lint that only applies to modules built via the official Move compiler.

### Finding Description
An unprivileged publisher who constructs module bytecode + `RuntimeModuleMetadataV1` directly (bypassing `aptos move build`/`ExtendedChecker`) can attach a `#[view]` `KnownAttribute` to any function as long as that function has a non-empty return signature. There is no on-chain check preventing that function from taking `&mut signer` or otherwise mutating global storage. Because `verify_module_metadata_for_module_publishing` is the only validation performed during publish, such a module is accepted and stored with metadata that misrepresents the function's mutation behavior. [1](#0-0) [4](#0-3) 

### Impact Explanation
Downstream tooling (indexers, explorers, wallets, SDKs) that trusts on-chain `fun_attributes`/`RuntimeModuleMetadataV1` (rather than re-deriving semantics from source) to decide that a function is side-effect-free could present a state-mutating (or, via `is_valid_unbiasable_function`, potentially biasable) function as safe to call speculatively/read-only, which is a metadata/behavior integrity issue for stored, verified code. Whether this translates into actual on-chain fund/state risk depends on whether the `/view` execution path (`construct_args`, `execute_view_function`) can actually be driven to invoke such a function with a `signer`, which I could not confirm in this session.

### Likelihood Explanation
Moderate: requires the attacker to hand-craft bytecode+metadata outside the standard compiler toolchain (feasible — metadata is just a BCS-encoded `Metadata` entry attached to the module, not something the VM re-derives from source), but the actual downstream impact is contingent on unverified execution-path behavior.

### Recommendation
Have Devin verify: (1) whether `transaction_arg_validation::construct_args`/`validate_view_function` and `execute_view_function` in `aptos-move/aptos-vm/src/aptos_vm.rs` reject a `signer` parameter type outright when invoked through the `/view` API, and (2) if not, extend `is_valid_view_function` in `types/src/vm/module_metadata.rs` to also reject functions whose parameter list contains `signer`/`&mut signer`, mirroring the `ExtendedChecker::check_and_record_view_functions` source-level checks, so the on-chain metadata guarantee matches what the compiler enforces.

### Proof of Concept
Not fully constructible from indexed context alone — building this PoC requires hand-assembling a `CompiledModule` (bypassing `aptos move compile`) with a function `fun mutate(s: &mut signer): u64 { move_to(s, R{}); 1 }`, attaching `RuntimeModuleMetadataV1{ fun_attributes: {"mutate": [KnownAttribute::view_function()]} }`, and publishing it via `code::publish_package_txn`, then asserting the module is accepted (no `AttributeValidationError`) and that `RuntimeModuleMetadataV1::fun_attributes` on-chain marks `mutate` as a view function despite taking `&mut signer`. A background Devin session with full repo/test-harness access (e.g. `aptos-move/e2e-move-tests`) would be needed to actually assemble and run this test to confirm the publish-time acceptance and separately determine whether `/view` execution of it succeeds or fails.

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

**File:** aptos-move/framework/src/extended_checks.rs (L753-806)
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

            // Remember the runtime info that this is a view function
            let module_id = self.get_runtime_module_id(module);
            self.output
                .entry(module_id)
                .or_default()
                .fun_attributes
                .entry(fun.get_simple_name_string().to_string())
                .or_default()
                .push(KnownAttribute::view_function());
        }
    }
```
