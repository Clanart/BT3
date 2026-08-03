Based on the code I found, the reported concern is already mitigated at the value layer, and the scenario described doesn't hold up.

**Analysis of the guard:**

`Locals::copy_loc` and `Locals::move_loc` explicitly check for `Value::Invalid` before ever returning a value to the caller: [1](#0-0) 

Both functions match `Some(Value::Invalid)` first and immediately return `PartialVMError::new(StatusCode::UNKNOWN_INVARIANT_VIOLATION_ERROR)` with an explicit "cannot copy/move invalid value at index" message — they never let an `Invalid` value escape into a real value position. This is exactly the "clean `PartialVMError`" outcome the question asks whether the system produces, and it already does, unconditionally, at the point where a local is read.

Separately, `borrow_loc` on a `Locals`/`Struct` container also rejects `Value::Invalid` explicitly rather than producing an `IndexedRef`/`ContainerRef` to it: [2](#0-1) 

**Why this can't reach `request_publish`'s native argument extraction:**

The `code: vector<vector<u8>>` argument is composed of Move `vector<u8>` elements. `vector<u8>` is a *specialized* primitive vector container (`Container::VecU8`), which stores raw `u8` values directly — it structurally cannot contain a `Value::Invalid` variant, since `Value::Invalid` only ever exists as a placeholder inside `Locals`/generic `Vec`/`Struct` containers, guarded by `check_valid_for_value_vector` (`ValueKind::RefOrInvalid` is rejected): [3](#0-2) 

In `native_request_publish`, each module's bytes are extracted via `module.value_as::<Vec<u8>>()`, operating on an already-constructed `Container::VecU8`, not on a `Locals` slot directly:

<invoke name="codebase_search">
<parameter name="query">placeholder</parameter>
</invoke>

### Citations

**File:** third_party/move/move-vm/types/src/values/values_impl.rs (L511-522)
```rust
    /// Returns an error if value's kind is not valid for [Container::Vec].
    fn check_valid_for_value_vector(&self) -> PartialVMResult<()> {
        use ValueKind as K;

        match self.kind() {
            K::NonSpecializedVecPrimitive | K::Container => Ok(()),
            K::SpecializedVecPrimitive | K::RefOrInvalid => {
                Err(PartialVMError::new(StatusCode::INTERNAL_TYPE_ERROR)
                    .with_message(format!("vector of `Value`s cannot contain {:?}", self)))
            },
        }
    }
```

**File:** third_party/move/move-vm/types/src/values/values_impl.rs (L2371-2404)
```rust
            // Borrowing from locals or structs produces IndexedRef for non-container values (e.g., primitives,
            // closures, delayed values). If the element is a container, we must produce ContainerRef.
            Container::Locals(r) | Container::Struct(r) => {
                let v = r.borrow();
                match &v[idx] {
                    Value::Container(container) => container_ref!(container),
                    Value::U8(_)
                    | Value::U16(_)
                    | Value::U32(_)
                    | Value::U64(_)
                    | Value::U128(_)
                    | Value::U256(_)
                    | Value::I8(_)
                    | Value::I16(_)
                    | Value::I32(_)
                    | Value::I64(_)
                    | Value::I128(_)
                    | Value::I256(_)
                    | Value::Bool(_)
                    | Value::Address(_)
                    | Value::ClosureValue(_)
                    | Value::DelayedFieldID { .. } => indexed_ref!(),

                    Value::ContainerRef(_) | Value::Invalid | Value::IndexedRef(_) => {
                        return Err(PartialVMError::new(
                            StatusCode::UNKNOWN_INVARIANT_VIOLATION_ERROR,
                        )
                        .with_message(format!(
                            "cannot borrow struct / locals element {:?}",
                            &v[idx]
                        )))
                    },
                }
            },
```

**File:** third_party/move/move-vm/types/src/values/values_impl.rs (L2580-2604)
```rust
    #[cfg_attr(feature = "inline-locals", inline(always))]
    pub fn copy_loc(&self, idx: usize) -> PartialVMResult<Value> {
        let locals = self.0.borrow();
        match locals.get(idx) {
            Some(Value::Invalid) => Err(PartialVMError::new(
                StatusCode::UNKNOWN_INVARIANT_VIOLATION_ERROR,
            )
            .with_message(format!("cannot copy invalid value at index {}", idx))),
            Some(v) => Ok(v.copy_value(1, Some(DEFAULT_MAX_VM_VALUE_NESTED_DEPTH))?),
            None => Err(Self::local_index_out_of_bounds(idx, locals.len())),
        }
    }

    #[cfg_attr(feature = "inline-locals", inline(always))]
    pub fn move_loc(&mut self, idx: usize) -> PartialVMResult<Value> {
        let mut locals = self.0.borrow_mut();
        match locals.get_mut(idx) {
            Some(Value::Invalid) => Err(PartialVMError::new(
                StatusCode::UNKNOWN_INVARIANT_VIOLATION_ERROR,
            )
            .with_message(format!("cannot move invalid value at index {}", idx))),
            Some(v) => Ok(std::mem::replace(v, Value::Invalid)),
            None => Err(Self::local_index_out_of_bounds(idx, locals.len())),
        }
    }
```
