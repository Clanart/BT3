[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** third_party/move/move-bytecode-verifier/src/dependencies.rs (L256-268)
```rust
                if !compatible_struct_abilities(struct_handle.abilities, def_handle.abilities)
                    || !compatible_struct_type_parameters(
                        &struct_handle.type_parameters,
                        &def_handle.type_parameters,
                    )
                {
                    return Err(verification_error(
                        StatusCode::TYPE_MISMATCH,
                        IndexKind::StructHandle,
                        idx as TableIndex,
                    )
                    .with_message("imported struct mismatches expectation"));
                }
```

**File:** third_party/move/move-bytecode-verifier/src/dependencies.rs (L449-457)
```rust
//  The local view of a type parameter must be a superset of (or equal to) the defined
//  constraints. Conceptually, the local view can be more constrained than the defined one as the
//  local context is only limiting usage, and cannot take advantage of the additional constraints.
fn compatible_type_parameter_constraints(
    local_type_parameter_constraints_declaration: AbilitySet,
    defined_type_parameter_constraints: AbilitySet,
) -> bool {
    defined_type_parameter_constraints.is_subset(local_type_parameter_constraints_declaration)
}
```

**File:** third_party/move/move-bytecode-verifier/src/dependencies.rs (L459-467)
```rust
// Adding phantom declarations relaxes the requirements for clients, thus, the local view may
// lack a phantom declaration present in the definition.
fn compatible_type_parameter_phantom_decl(
    local_type_parameter_declaration: &StructTypeParameter,
    defined_type_parameter: &StructTypeParameter,
) -> bool {
    // local_type_parameter_declaration.is_phantom => defined_type_parameter.is_phantom
    !local_type_parameter_declaration.is_phantom || defined_type_parameter.is_phantom
}
```

**File:** third_party/move/move-bytecode-verifier/src/dependencies.rs (L574-597)
```rust
fn compare_structs(
    context: &Context,
    idx1: StructHandleIndex,
    idx2: StructHandleIndex,
    def_module: &CompiledModule,
) -> PartialVMResult<()> {
    // grab ModuleId and struct name for the module being verified
    let struct_handle = context.resolver.struct_handle_at(idx1);
    let module_handle = context.resolver.module_handle_at(struct_handle.module);
    let module_id = context.resolver.module_id_for_handle(module_handle);
    let struct_name = context.resolver.identifier_at(struct_handle.name);

    // grab ModuleId and struct name for the definition
    let def_struct_handle = def_module.struct_handle_at(idx2);
    let def_module_handle = def_module.module_handle_at(def_struct_handle.module);
    let def_module_id = def_module.module_id_for_handle(def_module_handle);
    let def_struct_name = def_module.identifier_at(def_struct_handle.name);

    if module_id != def_module_id || struct_name != def_struct_name {
        Err(PartialVMError::new(StatusCode::TYPE_MISMATCH))
    } else {
        Ok(())
    }
}
```
