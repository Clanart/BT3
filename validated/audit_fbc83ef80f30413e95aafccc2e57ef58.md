[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/aptos-vm/src/verifier/native_validation.rs (L12-27)
```rust
pub(crate) fn validate_module_natives(modules: &[CompiledModule]) -> VMResult<()> {
    for module in modules {
        let module_address = module.self_addr();
        for native in module.function_defs().iter().filter(|def| def.is_native()) {
            if native.is_entry || !module_address.is_special() {
                return Err(
                    PartialVMError::new(StatusCode::USER_DEFINED_NATIVE_NOT_ALLOWED)
                        .with_message(
                            "Cannot publish native function to non-special address".to_string(),
                        )
                        .finish(Location::Module(module.self_id())),
                );
            }
        }
    }
    Ok(())
```

**File:** third_party/move/move-bytecode-verifier/src/instruction_consistency.rs (L228-242)
```rust
    fn check_function_op(
        &self,
        offset: usize,
        func_handle_index: FunctionHandleIndex,
        generic: bool,
    ) -> PartialVMResult<()> {
        let function_handle = self.resolver.function_handle_at(func_handle_index);
        if function_handle.type_parameters.is_empty() == generic {
            return Err(
                PartialVMError::new(StatusCode::GENERIC_MEMBER_OPCODE_MISMATCH)
                    .at_code_offset(self.current_function(), offset as CodeOffset),
            );
        }
        Ok(())
    }
```

**File:** third_party/move/move-bytecode-verifier/bytecode-verifier-tests/src/unit_tests/generic_ops_tests.rs (L200-238)
```rust
#[test]
fn generic_call_to_non_generic_func() {
    let mut module = make_module();
    // bogus `CallGeneric fn()`
    module.function_defs[2].code = Some(CodeUnit {
        locals: SignatureIndex(0),
        code: vec![
            Bytecode::CallGeneric(FunctionInstantiationIndex(0)),
            Bytecode::Ret,
        ],
    });
    module.function_instantiations.push(FunctionInstantiation {
        handle: FunctionHandleIndex(0),
        type_parameters: SignatureIndex(2),
    });
    module.signatures.push(Signature(vec![SignatureToken::U64]));
    let err = InstructionConsistency::verify_module(&module)
        .expect_err("CallGeneric to non generic function must fail");
    assert_eq!(
        err.major_status(),
        StatusCode::GENERIC_MEMBER_OPCODE_MISMATCH
    );
}

#[test]
fn non_generic_call_to_generic_func() {
    let mut module = make_module();
    // bogus `Call g_fn<T>()`
    module.function_defs[2].code = Some(CodeUnit {
        locals: SignatureIndex(0),
        code: vec![Bytecode::Call(FunctionHandleIndex(1)), Bytecode::Ret],
    });
    let err = InstructionConsistency::verify_module(&module)
        .expect_err("Call to generic function must fail");
    assert_eq!(
        err.major_status(),
        StatusCode::GENERIC_MEMBER_OPCODE_MISMATCH
    );
}
```
