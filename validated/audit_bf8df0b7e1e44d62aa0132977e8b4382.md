[1](#0-0) [2](#0-1)

### Citations

**File:** third_party/move/move-vm/runtime/src/frame_type_cache.rs (L65-72)
```rust
    /// Caches function and its cache for non-generic handles. Uses weak reference for cache to
    /// prevent memory leaks for recursive functions.
    pub(crate) function_cache:
        BTreeMap<FunctionHandleIndex, (Rc<LoadedFunction>, Weak<RefCell<FrameTypeCache>>)>,
    /// Caches function and its cache for generic handles. Like function cache, uses weak reference
    /// for cache to prevent memory leaks for recursive functions.
    pub(crate) generic_function_cache:
        BTreeMap<FunctionInstantiationIndex, (Rc<LoadedFunction>, Weak<RefCell<FrameTypeCache>>)>,
```

**File:** third_party/move/move-vm/runtime/src/frame_type_cache.rs (L230-238)
```rust
    pub(crate) fn make_rc_for_function(function: &LoadedFunction) -> Rc<RefCell<Self>> {
        let frame_cache = Rc::new(RefCell::<Self>::new(Default::default()));

        frame_cache
            .borrow_mut()
            .per_instruction_cache
            .resize(function.code_size(), PerInstructionCache::Nothing);
        frame_cache
    }
```
