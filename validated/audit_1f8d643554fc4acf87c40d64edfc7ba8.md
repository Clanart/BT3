[1](#0-0) [2](#0-1)

### Citations

**File:** third_party/move/move-binary-format/src/constant.rs (L72-75)
```rust
    pub fn deserialize_constant(&self) -> Option<MoveValue> {
        let ty = sig_to_ty(&self.type_)?;
        MoveValue::simple_deserialize(&self.data, &ty).ok()
    }
```

**File:** third_party/move/move-bytecode-verifier/src/constants.rs (L56-65)
```rust
fn verify_constant_data(idx: usize, constant: &Constant) -> PartialVMResult<()> {
    match constant.deserialize_constant() {
        Some(_) => Ok(()),
        None => Err(verification_error(
            StatusCode::MALFORMED_CONSTANT_DATA,
            IndexKind::ConstantPool,
            idx as TableIndex,
        )),
    }
}
```
