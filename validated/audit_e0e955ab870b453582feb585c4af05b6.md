[1](#0-0) [2](#0-1)

### Citations

**File:** third_party/move/move-vm/runtime/src/move_vm.rs (L119-136)
```rust
        let return_values = {
            let _timer = VM_TIMER.timer_with_label("Interpreter::entrypoint");

            Interpreter::entrypoint(
                function,
                deserialized_args,
                data_cache,
                // TODO(caches): async drop
                &mut InterpreterFunctionCaches::new(),
                loader,
                &ty_depth_checker,
                &layout_converter,
                gas_meter,
                traversal_context,
                extensions,
                trace_recorder,
            )?
        };
```

**File:** third_party/move/move-vm/runtime/src/loader/function.rs (L209-218)
```rust
/// Stable pointer identity for a non-generic [Function] within a single interpreter invocation.
#[derive(Copy, Clone, Eq, PartialEq, Debug)]
pub(crate) struct FunctionPtr(*const Function);

impl FunctionPtr {
    pub(crate) fn from_loaded_function(function: &LoadedFunction) -> Self {
        // Pointer identity can be used since the loader guarantees that any loaded function has
        // exactly one `Arc<Function>`.
        Self(Arc::as_ptr(&function.function))
    }
```
