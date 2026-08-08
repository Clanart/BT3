## Title
Undefined behavior from unvalidated boolean fields when translating CPI `AccountInfo`/`SolAccountInfo` arrays - (File: `program-runtime/src/cpi.rs`)

### Summary
The CPI account-info translation path (`translate_account_infos`) constructs safe Rust references (`&[AccountInfo]` / `&[SolAccountInfo]`) directly over attacker-controlled VM guest memory via `translate_slice::<T>`, without validating that the raw bytes backing the `bool` fields (`is_signer`, `is_writable`, `executable`) are actually `0` or `1`. This is the exact same bug class as the OrderNFT `decodeId()` issue — an attacker-supplied byte is implicitly, ambiguously coerced into a semantically-typed value (there, `bool` isBid; here, Rust `bool` fields) without validating the value is one of the two legal encodings. Elsewhere in the very same file, the developers explicitly patched this exact issue for `AccountMeta` translation but the fix was not applied to the `AccountInfo`/`SolAccountInfo` translation path used for every CPI call.

### Finding Description
`program-runtime/src/cpi.rs` defines the CPI-facing structs with plain `bool` fields: [1](#0-0) 

When a program performs a CPI (`sol_invoke_signed_rust` / `sol_invoke_signed_c`), the account-info array the *calling* program passes is translated through `translate_account_infos`, which calls `translate_slice::<T>` and then indexes directly into it as `&T`: [2](#0-1) 

`translate_slice` performs only bounds/alignment checks on the raw memory range and then reinterprets the bytes as a safe `&[T]` reference: [3](#0-2) 

Because the calling program fully controls the bytes at that VM address (it is the program's own account-info buffer, populated by BPF bytecode the caller controls), it can set the byte(s) backing `is_signer`, `is_writable`, or `executable` to any value other than `0x00`/`0x01` (e.g. `0x02`). Constructing a safe `&bool` reference (or a struct containing one) whose backing byte is not `0` or `1` is immediate, unconditional Rust undefined behavior, independent of whether the invalid value is ever read.

This is not a hypothetical, unaddressed pattern in this codebase — the developers explicitly recognized and fixed the identical problem for `AccountMeta` (32-byte pubkey + `is_signer: bool` + `is_writable: bool`) translated during `translate_instruction_rust` / `translate_instruction_c`, using a `MaybeUninit`-based translation plus an explicit volatile byte-range check before calling `assume_init_ref`: [4](#0-3) [5](#0-4) 

That guard is present specifically "to prevent UB," proving this exact bug class was previously identified and remediated for `AccountMeta`. However, `translate_account_infos` (used by both `translate_accounts_rust` and `translate_accounts_c`, i.e. every CPI account-info array) still uses the unguarded `translate_slice::<T>` path with no equivalent boolean-byte validation: [6](#0-5) [7](#0-6) [8](#0-7) 

### Impact Explanation
This is reachable by any unprivileged on-chain BPF program that performs a cross-program invocation — no special privileges are required. The root cause is a violated Rust safety invariant (`bool` must be `0` or `1`) at the moment the reference is materialized in the validator host process, which the Rust language specification treats as immediate undefined behavior. In practice this can manifest as non-deterministic optimizer behavior (since LLVM assumes `bool` ∈ {0,1} for branch elimination/vectorization), incorrect code paths being taken across different compiler versions/optimization levels, or a validator crash — any of which threatens the "same input, same output" determinism guarantee central to consensus, and can cause node crashes/panics under specific compiler-optimized builds. This matches the accepted "non-deterministic execution" and "VM-triggered node crash" impact categories.

### Likelihood Explanation
Any user-deployed BPF program can trivially construct a raw byte buffer for its `SolInstruction`/`AccountInfo` array with garbage boolean bytes and issue `sol_invoke_signed_c`/`sol_invoke_signed_rust`; no privileged role, specific validator configuration, or race condition is required. This makes the precondition trivially satisfiable by an ordinary transaction.

### Recommendation
Apply the same mitigation already used for `AccountMeta` translation (`program-runtime/src/cpi.rs:577-594`, `713-727`) to the `AccountInfo`/`SolAccountInfo` translation path in `translate_account_infos`: translate the account-info slice as `&[MaybeUninit<T>]`, explicitly validate (e.g. via volatile byte reads) that each `is_signer`/`is_writable`/`executable` byte is `0` or `1` before calling `assume_init_ref`/using the value, and return `InstructionError::InvalidArgument` (or similar) otherwise.

### Proof of Concept
1. Deploy a BPF program that, before invoking `sol_invoke_signed_c` (or the Rust CPI helper), manually writes its `SolAccountInfo`/`AccountInfo` array into memory such that the byte at the `is_signer` (or `is_writable`/`executable`) field offset is set to `2` instead of `0`/`1`.
2. Invoke a CPI (`invoke` / `invoke_signed`) referencing that account-info array.
3. The validator's `translate_account_infos` (`program-runtime/src/cpi.rs:925-950`) calls `translate_slice::<AccountInfo>`/`translate_slice::<SolAccountInfo>`, producing a safe reference whose `bool` field has an invalid bit pattern — undefined behavior in the host process, which (depending on compiler/optimization level) can manifest as incorrect branching or a crash, unlike the already-hardened `AccountMeta` path which explicitly rejects this input with `InstructionError::InvalidArgument`.

### Citations

**File:** program-runtime/src/cpi.rs (L90-103)
```rust
/// Rust representation of C's SolAccountInfo
#[derive(Debug)]
#[repr(C)]
struct SolAccountInfo {
    pub key_addr: u64,
    pub lamports_addr: u64,
    pub data_len: u64,
    pub data_addr: u64,
    pub owner_addr: u64,
    pub rent_epoch: u64,
    pub is_signer: bool,
    pub is_writable: bool,
    pub executable: bool,
}
```

**File:** program-runtime/src/cpi.rs (L577-594)
```rust
    let mut accounts = Vec::with_capacity(account_metas.len());
    for account_meta in account_metas {
        // Before using `account_meta` directly, verify that `is_signer` and `is_writable`
        // contain valid boolean values to prevent UB.
        let account_meta = unsafe {
            let ptr = account_meta.as_ptr();
            if (&raw const (*ptr).is_signer).cast::<u8>().read_volatile() > 1
                || (&raw const (*ptr).is_writable).cast::<u8>().read_volatile() > 1
            {
                return Err(Box::new(InstructionError::InvalidArgument));
            }
            // SAFETY: VM memory is initialized, and we have validated that the boolean fields
            // contain valid data.
            account_meta.assume_init_ref()
        };

        accounts.push(account_meta.clone());
    }
```

**File:** program-runtime/src/cpi.rs (L603-629)
```rust
pub fn translate_accounts_rust<'a>(
    account_infos_addr: u64,
    account_infos_len: u64,
    invoke_context: &InvokeContext,
) -> Result<Vec<TranslatedAccount<'a>>, Error> {
    let check_aligned = invoke_context.get_check_aligned();
    let memory_mapping = invoke_context.memory_contexts.memory_mapping()?;
    translate_account_infos(
        account_infos_addr,
        account_infos_len,
        |account_info: &AccountInfo| account_info.key as *const _ as u64,
        invoke_context,
        memory_mapping,
        check_aligned,
        |account_infos, account_info_keys| {
            translate_accounts_common(
                &account_info_keys,
                account_infos,
                account_infos_addr,
                invoke_context,
                memory_mapping,
                check_aligned,
                CallerAccount::from_account_info,
            )
        },
    )?
}
```

**File:** program-runtime/src/cpi.rs (L713-727)
```rust
    let mut accounts = Vec::with_capacity(ix_c.accounts_len as usize);
    for account_meta in account_metas {
        // Before using `account_meta` directly, verify that `is_signer` and `is_writable`
        // contain valid boolean values to prevent UB.
        let account_meta = unsafe {
            let ptr = account_meta.as_ptr();
            if (&raw const (*ptr).is_signer).cast::<u8>().read_volatile() > 1
                || (&raw const (*ptr).is_writable).cast::<u8>().read_volatile() > 1
            {
                return Err(Box::new(InstructionError::InvalidArgument));
            }
            // SAFETY: VM memory is initialized, and we have validated that the boolean fields
            // contain valid data.
            account_meta.assume_init_ref()
        };
```

**File:** program-runtime/src/cpi.rs (L744-770)
```rust
pub fn translate_accounts_c<'a>(
    account_infos_addr: u64,
    account_infos_len: u64,
    invoke_context: &InvokeContext,
) -> Result<Vec<TranslatedAccount<'a>>, Error> {
    let check_aligned = invoke_context.get_check_aligned();
    let memory_mapping = invoke_context.memory_contexts.memory_mapping()?;
    translate_account_infos(
        account_infos_addr,
        account_infos_len,
        |account_info: &SolAccountInfo| account_info.key_addr,
        invoke_context,
        memory_mapping,
        check_aligned,
        |account_infos, account_info_keys| {
            translate_accounts_common(
                &account_info_keys,
                account_infos,
                account_infos_addr,
                invoke_context,
                memory_mapping,
                check_aligned,
                CallerAccount::from_sol_account_info,
            )
        },
    )?
}
```

**File:** program-runtime/src/cpi.rs (L901-951)
```rust
fn translate_account_infos<T, R>(
    account_infos_addr: u64,
    account_infos_len: u64,
    key_addr: impl Fn(&T) -> u64,
    invoke_context: &InvokeContext,
    memory_mapping: &MemoryMapping,
    check_aligned: bool,
    cb: impl FnOnce(&[T], Vec<&Pubkey>) -> R,
) -> Result<R, Error> {
    let syscall_parameter_address_restrictions = invoke_context
        .get_feature_set()
        .syscall_parameter_address_restrictions;

    // In the same vein as the other check_account_info_pointer() checks, we don't lock
    // this pointer to a specific address but we don't want it to be inside accounts, or
    // callees might be able to write to the pointed memory.
    if syscall_parameter_address_restrictions
        && account_infos_addr
            .saturating_add(account_infos_len.saturating_mul(std::mem::size_of::<T>() as u64))
            >= ebpf::MM_INPUT_START
    {
        return Err(CpiError::InvalidPointer.into());
    }

    let account_infos = translate_slice::<T>(
        memory_mapping,
        account_infos_addr,
        account_infos_len,
        check_aligned,
    )?;
    check_account_infos(account_infos.len())?;

    let account_infos_bytes = account_infos.len().saturating_mul(ACCOUNT_INFO_BYTE_SIZE);

    let amount = (account_infos_bytes as u64)
        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
        .unwrap_or(u64::MAX);
    invoke_context.compute_meter.consume_checked(amount)?;

    let mut account_info_keys = Vec::with_capacity(account_infos_len as usize);
    #[expect(clippy::needless_range_loop)]
    for account_index in 0..account_infos_len as usize {
        #[expect(clippy::indexing_slicing)]
        let account_info = &account_infos[account_index];
        account_info_keys.push(translate_type::<Pubkey>(
            memory_mapping,
            key_addr(account_info),
            check_aligned,
        )?);
    }
    Ok(cb(account_infos, account_info_keys))
```

**File:** program-runtime/src/memory.rs (L126-154)
```rust
pub fn translate_type<'a, T>(
    memory_mapping: &MemoryMapping,
    vm_addr: u64,
    check_aligned: bool,
) -> Result<&'a T, Box<dyn std::error::Error>> {
    translate_type_inner!(memory_mapping, AccessType::Load, vm_addr, T, check_aligned)
}

pub fn translate_slice<T>(
    memory_mapping: &MemoryMapping,
    vm_addr: u64,
    len: u64,
    check_aligned: bool,
) -> Result<&[T], Box<dyn std::error::Error>> {
    translate_slice_inner!(
        memory_mapping,
        AccessType::Load,
        vm_addr,
        len,
        T,
        check_aligned,
    )
    .map(|value| unsafe {
        // SAFETY: `translate_slice_inner` is guaranteed to return a dereferenceable memory region.
        // This is producing a shared/read-only slice to the memory, so the uniqueness invariants
        // aren't relevant.
        &*value
    })
}
```
