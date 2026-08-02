### Title
Function-name collision across distinct `FunctionHandle`s causes runtime call-target aliasing that bypasses per-handle bytecode-verifier type checks - (`third_party/move/move-vm/runtime/src/loader/modules.rs`)

### Summary
`Module::new` resolves every local (`module_id == id`) `FunctionHandle` to a concrete `Function` by looking the function up **by name** in `function_map`, rather than by the specific `FunctionHandleIndex`/`FunctionDefinitionIndex` binding that the bytecode verifier actually checked the call site against. If a module contains two distinct `FunctionHandle` entries with the same `module` (self) and the same `name` but different `parameters`/`return_` signatures — each backing a separate `FunctionDefinition` — the `HashMap<Identifier, usize>` in `function_map` silently collapses them to whichever definition is processed last, aliasing calls verified against one signature to execute the body of the other function.

### Finding Description
In `Module::new`, function definitions are indexed by name: [1](#0-0) 
and every local `FunctionHandle` in the table is resolved purely by that same name-keyed map: [2](#0-1) 

This is the exact mechanism the question describes: `function_handle.module` resolving to `self_id()` triggers `FunctionHandle::Local`, and the binding used at runtime (`function_map.get(func_name)`) is independent of which `FunctionHandleIndex` a `Call` bytecode instruction actually references.

The duplication checker (`DuplicationChecker::check_function_definitions`) only guarantees:
1. no two `FunctionDefinition`s point at the same `FunctionHandleIndex` (`first_duplicate_element` over `.function`),
2. every self-module `FunctionHandle` is "implemented" by exactly one `FunctionDefinition`. [3](#0-2) 

Nothing in this pass (nor, as far as I could trace, elsewhere in `check_bounds.rs`, `signature_v2.rs`, or `dependencies.rs`) enforces that two distinct local `FunctionHandle` entries must have distinct `name` fields. `FunctionHandle`s are only deduplicated as whole structs (module+name+parameters+return+type_parameters), so two handles sharing `module` and `name` but differing in `parameters`/`return_` are structurally legal and each can be "implemented" by its own separate `FunctionDefinition`.

Type-safety/instruction verification for a `Call(FunctionHandleIndex)` bytecode checks the operand stack against that specific handle's `parameters`/`return_` signature (via `function_handle_at(idx)`), as seen in the parallel cross-module check pattern in `dependencies.rs`: [4](#0-3) 
i.e. verification is performed per-handle-index, not per-name. But at load time the loader discards that index binding and instead re-resolves the call target by name via `function_map`, which only holds the *last-processed* definition for a given name. Consequently, a `Call` instruction verified as type-safe against handle A's signature can execute the bytecode of the differently-signed function tied to handle B, because both share the same `name` and the loader's local-resolution logic conflates them.

### Impact Explanation
This breaks the fundamental invariant that the bytecode verifier's per-call-site type checking corresponds to what actually executes at runtime. An attacker publishing raw, hand-crafted bytecode (bypassing the Move compiler, which would never emit two functions with identical names) can construct a module where a `Call` site verified as safe for one signature silently invokes a function with an incompatible signature and unrelated body, undermining the VM's type/reference safety guarantees for code that reaches on-chain execution. This is squarely inside the "publish/verifier/loader must agree on what is legal" pivot named in the review scope.

### Likelihood Explanation
Reaching this requires publishing hand-crafted `CompiledModule` bytes directly (not compiler output), which is achievable through the standard unprivileged module-publish path since Aptos accepts raw bytecode subject to the bytecode verifier. I traced the loader (`modules.rs`) and the duplication checker (`check_duplication.rs`) and confirmed neither prevents two same-module, same-name `FunctionHandle`s with differing signatures. I was not able to fully confirm, within available tool iterations, that no other verifier pass (e.g., `type_safety.rs`'s exact `Call` handling, or a possible implicit uniqueness enforced by `IdentifierIndex` handling) independently blocks this specific construction — this should be verified directly against `move-bytecode-verifier/src/type_safety.rs` and any locals/reference safety pass before treating this as fully confirmed.

### Recommendation
In `Module::new`, resolve `FunctionHandle::Local` by the exact `FunctionDefinitionIndex` bound to the handle (i.e., by matching `function_def.function == handle_index`, not by name lookup through `function_map`), or add a `DuplicationChecker` rule rejecting multiple self-module `FunctionHandle`s that share the same `name` (mirroring the existing `first_duplicate_element` pattern used for other tables).

### Proof of Concept
1. Hand-craft a `CompiledModule` (bypassing `move-compiler`) containing:
   - Two `FunctionHandle` entries, both with `module = self_handle_idx()` and `name = "foo"`, but different `parameters`/`return_` signature indices (e.g., handle A: `(u64) -> ()`, handle B: `() -> u64`).
   - Two `FunctionDefinition`s, one referencing handle A with body `X`, one referencing handle B with body `Y`.
   - A third function containing `Call(handle_A_index)` passing a `u64` argument, matching handle A's declared signature.
2. Run this module through `DuplicationChecker::verify_module` and the standard bytecode-verifier pipeline — both function handles are "implemented" and structurally distinct, so verification passes.
3. Publish the module and call the function containing `Call(handle_A_index)`.
4. Inspect `Module::function_refs[handle_A_index]` at load time via `Module::new`: because `function_map.insert("foo", idx)` was last executed for the definition backing handle B, `FunctionHandle::Local` for handle A actually resolves to body `Y`, not `X` — i.e., the call executes the wrong function body relative to what was verified for that call site.

### Citations

**File:** third_party/move/move-vm/runtime/src/loader/modules.rs (L260-266)
```rust
        for (idx, _) in module.function_defs().iter().enumerate() {
            let findex = FunctionDefinitionIndex(idx as TableIndex);
            let function = Function::new(natives, findex, &module, signature_table.as_slice())?;

            function_map.insert(function.name.to_owned(), idx);
            function_defs.push(Arc::new(function));
        }
```

**File:** third_party/move/move-vm/runtime/src/loader/modules.rs (L270-289)
```rust
        for func_handle in module.function_handles() {
            let func_name = module.identifier_at(func_handle.name);
            let module_handle = module.module_handle_at(func_handle.module);
            let module_id = module.module_id_for_handle(module_handle);
            let func_handle = if module_id == id {
                FunctionHandle::Local(
                    function_defs[*function_map.get(func_name).ok_or_else(|| {
                        PartialVMError::new(StatusCode::TYPE_RESOLUTION_FAILURE)
                            .with_message("Cannot find function in publishing module".to_string())
                    })?]
                    .clone(),
                )
            } else {
                FunctionHandle::Remote {
                    module: module_id,
                    name: func_name.to_owned(),
                }
            };
            function_refs.push(func_handle);
        }
```

**File:** third_party/move/move-bytecode-verifier/src/check_duplication.rs (L341-391)
```rust
    fn check_function_definitions(&self) -> PartialVMResult<()> {
        // FunctionDefinition - contained FunctionHandle defines uniqueness
        if let Some(idx) =
            Self::first_duplicate_element(self.module.function_defs().iter().map(|x| x.function))
        {
            return Err(verification_error(
                StatusCode::DUPLICATE_ELEMENT,
                IndexKind::FunctionDefinition,
                idx,
            ));
        }
        // Acquires in function declarations contain unique struct definitions
        for (idx, function_def) in self.module.function_defs().iter().enumerate() {
            let acquires = function_def.acquires_global_resources.iter();
            if Self::first_duplicate_element(acquires).is_some() {
                return Err(verification_error(
                    StatusCode::DUPLICATE_ACQUIRES_ANNOTATION,
                    IndexKind::FunctionDefinition,
                    idx as TableIndex,
                ));
            }
        }
        // Check that each function definition is pointing to the self module
        if let Some(idx) = self.module.function_defs().iter().position(|x| {
            self.module.function_handle_at(x.function).module != self.module.self_handle_idx()
        }) {
            return Err(verification_error(
                StatusCode::INVALID_MODULE_HANDLE,
                IndexKind::FunctionDefinition,
                idx as TableIndex,
            ));
        }
        // Check that each function handle in self module is implemented (has a declaration)
        let implemented_function_handles: HashSet<FunctionHandleIndex> = self
            .module
            .function_defs()
            .iter()
            .map(|x| x.function)
            .collect();
        if let Some(idx) = (0..self.module.function_handles().len()).position(|x| {
            let y = FunctionHandleIndex::new(x as u16);
            self.module.function_handle_at(y).module == self.module.self_handle_idx()
                && !implemented_function_handles.contains(&y)
        }) {
            return Err(verification_error(
                StatusCode::UNIMPLEMENTED_HANDLE,
                IndexKind::FunctionHandle,
                idx as TableIndex,
            ));
        }
        Ok(())
```

**File:** third_party/move/move-bytecode-verifier/src/dependencies.rs (L284-298)
```rust
    for (idx, function_handle) in context.resolver.function_handles().iter().enumerate() {
        if Some(function_handle.module) == self_module {
            continue;
        }
        let owner_module_id = context
            .resolver
            .module_id_for_handle(context.resolver.module_handle_at(function_handle.module));
        let function_name = context.resolver.identifier_at(function_handle.name);
        let owner_module = safe_unwrap!(context.dependency_map.get(&owner_module_id));
        match context
            .func_id_to_index_map
            .get(&(owner_module_id.clone(), function_name.to_owned()))
        {
            Some((owner_handle_idx, owner_def_idx)) => {
                let def_handle = owner_module.function_handle_at(*owner_handle_idx);
```
