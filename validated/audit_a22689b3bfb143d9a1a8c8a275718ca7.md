### Title
Hardcoded `check_aligned = false` when translating the CPI `AccountInfo` length pointer allows misaligned `&mut u64` dereference - ([File: program-runtime/src/cpi.rs])

### Summary
`CallerAccount::from_account_info` and `CallerAccount::from_sol_account_info` compute the VM address of an account's serialized length field (`vm_len_addr`) and translate it into a host `&mut u64` reference via `translate_type_mut_for_cpi`, but they pass a hardcoded `false` for `check_aligned` instead of forwarding the caller's real `check_aligned` value. This mirrors the exact bug class described in the external Wasmer/Stylus report: skipping alignment validation before dereferencing a pointer as a wider-than-byte type, producing undefined behavior when the pointer is not naturally aligned.

### Finding Description
`program-runtime/src/memory.rs`'s `translate_type_inner!` macro is specifically designed to prevent misaligned dereferences: when `check_aligned` is `true` it validates `ptr.is_aligned()` before returning a reference, and only skips the check when `check_aligned` is `false` (used deliberately for byte-sized `u8` accesses, e.g. `translate_slice_mut_for_cpi::<u8>(..., false /* Don't care since it is byte aligned */)` at [1](#0-0) ).

However, for the `u64` length-field pointer, both `from_account_info` and `from_sol_account_info` hardcode `false` regardless of the actual loader/alignment requirement: [2](#0-1) [3](#0-2) 

Unlike the `u8` cases, there is no "byte aligned" justification here — `u64` has a natural alignment requirement of 8 bytes. `vm_len_addr` is derived directly from `account_info.data.as_ptr()` (or the analogous `SolAccountInfo` struct offset), whose value is fully attacker-controlled: a BPF program (running under the non-deprecated `bpf_loader`, where `get_check_aligned()` returns `true`, [4](#0-3) ) can construct its own `AccountInfo`/`SolAccountInfo` in guest memory with a `data` slice pointer that is intentionally not 8-byte aligned before invoking a CPI (`invoke`/`invoke_signed`). Because `check_aligned=false` is hardcoded for this one translation call, `translate_type_inner!`'s alignment guard is bypassed and `unsafe { ptr.as_mut_unchecked() }` is returned unconditionally, producing a `&'a mut u64` (`ref_to_len_in_vm`) that may point to a misaligned host address.

This reference is then dereferenced multiple times: read at `*ref_to_len_in_vm` when computing the account's data length ( [5](#0-4) ), and it is stored in `CallerAccount` for later use/write-back after the CPI call completes ( [6](#0-5) ). Dereferencing a `&mut u64`/`&u64` through a misaligned pointer is undefined behavior in Rust — the exact bug class flagged in the report ("misaligned pointer dereference: address must be a multiple of 0x8").

### Impact Explanation
This is reachable from unprivileged, on-chain BPF program code performing a normal CPI call — no validator/operator privilege is required. The consequence is undefined behavior on a hot, consensus-critical path (every CPI call goes through this code): depending on platform/compiler behavior this can manifest as a validator crash (panic/segfault under debug or `cargo-careful`/sanitizer builds, or on strict-alignment targets), or non-deterministic codegen (e.g., compiler-generated SIMD/vectorized loads assuming alignment) causing inconsistent behavior across compiler versions or optimization levels — a correctness/availability risk for the fleet.

### Likelihood Explanation
Reasonably likely to be reachable: `AccountInfo`/`SolAccountInfo` structures passed to `invoke`/`invoke_signed` are entirely program-supplied data structures; a malicious or buggy program can trivially craft a `data: &[u8]` (or `SolAccountInfo.data_addr`) whose base pointer is not 8-byte aligned (e.g., pointing into the middle of a buffer), causing `vm_len_addr = data.as_ptr() + 8` to also be misaligned. No special validator configuration or feature-flag state is needed beyond a program using the standard (non-deprecated) BPF loader, which is the common case.

### Recommendation
Pass the real `check_aligned` value into `translate_type_mut_for_cpi::<u64>` for `vm_len_addr` at both call sites (lines 376 and 496) instead of hardcoding `false`, so the alignment check performed by `translate_type_inner!` is actually enforced, matching the behavior of the sibling `lamports`/`owner` translations in the same functions. Add a regression test constructing an `AccountInfo`/`SolAccountInfo` with a deliberately misaligned `data` pointer under a non-deprecated-loader program and confirm the syscall now returns `SyscallError::UnalignedPointer` instead of proceeding with UB. Additionally run the CPI test suite under `cargo +nightly careful test` / debug assertions in CI to catch this class of issue going forward, as recommended in the source report.

### Proof of Concept
Conceptual PoC (cannot be executed without a live validator/test harness):
1. Write a BPF program (loaded under the standard `bpf_loader_upgradeable`, so `check_aligned == true`) that manually constructs an `AccountInfo` (or raw `SolAccountInfo`) whose `data` field is a `&[u8]` built via `slice::from_raw_parts(misaligned_ptr, len)`, where `misaligned_ptr` is deliberately offset to not be 8-byte aligned relative to the input buffer.
2. Invoke `invoke()`/`invoke_signed()` (or the raw `sol_invoke_signed_c`/`sol_invoke_signed_rust` syscall) passing this crafted `AccountInfo`.
3. In `CallerAccount::from_account_info` (or `from_sol_account_info`), `vm_len_addr = data.as_ptr() + 8` inherits the misalignment, and `translate_type_mut_for_cpi::<u64>(memory_mapping, vm_len_addr, false)` skips the alignment check and returns a misaligned `&mut u64`.
4. Under a debug build / `cargo careful` build (or on a strict-alignment target), dereferencing `*ref_to_len_in_vm` panics/faults analogous to the Wasmer report's "misaligned pointer dereference" error; under a normal release x86-64 build, this is silent UB that could produce incorrect codegen under future compiler optimizations.

### Citations

**File:** program-runtime/src/cpi.rs (L189-207)
```rust
pub struct CallerAccount<'a> {
    pub lamports: &'a mut u64,
    pub owner: &'a mut Pubkey,
    // The original data length of the account at the start of the current
    // instruction. We use this to determine whether an account was shrunk or
    // grown before or after CPI, and to derive the vm address of the realloc
    // region.
    pub original_data_len: usize,
    // This points to the data section for this account, as serialized and
    // mapped inside the vm (see serialize_parameters() in
    // BpfExecutor::execute).
    //
    // This is only set when account_data_direct_mapping is off.
    pub serialized_data: &'a mut [u8],
    // Given the corresponding input AccountInfo::data, vm_data_addr points to
    // the pointer field and ref_to_len_in_vm points to the length field.
    pub vm_data_addr: u64,
    pub ref_to_len_in_vm: &'a mut u64,
}
```

**File:** program-runtime/src/cpi.rs (L243-267)
```rust
            unsafe {
                // SAFETY: Invariants for constructing a mutable reference delegated to the caller.
                let serialization_ptr: &'a mut [u8] = translate_slice_mut_for_cpi::<u8>(
                    memory_mapping,
                    solana_sbpf::ebpf::MM_INPUT_START,
                    1,
                    false, // Don't care since it is byte aligned
                )?;
                Ok(std::slice::from_raw_parts_mut(
                    serialization_ptr
                        .as_mut_ptr()
                        .add(vm_addr.saturating_sub(solana_sbpf::ebpf::MM_INPUT_START) as usize),
                    len,
                ))
            }
        } else {
            unsafe {
                // SAFETY: Invariants for constructing a mutable reference delegated to the caller.
                translate_slice_mut_for_cpi::<u8>(
                    memory_mapping,
                    vm_addr,
                    len as u64,
                    false, // Don't care since it is byte aligned
                )
            }
```

**File:** program-runtime/src/cpi.rs (L365-376)
```rust
            let vm_len_addr = (account_info.data.as_ptr() as *const u64 as u64)
                .saturating_add(std::mem::size_of::<u64>() as u64);
            if syscall_parameter_address_restrictions {
                // In the same vein as the other check_account_info_pointer() checks, we don't lock
                // this pointer to a specific address but we don't want it to be inside accounts, or
                // callees might be able to write to the pointed memory.
                if vm_len_addr >= solana_sbpf::ebpf::MM_INPUT_START {
                    return Err(Box::new(CpiError::InvalidPointer));
                }
            }
            let ref_to_len_in_vm =
                translate_type_mut_for_cpi::<u64>(memory_mapping, vm_len_addr, false)?;
```

**File:** program-runtime/src/cpi.rs (L384-393)
```rust
                    if syscall_parameter_address_restrictions {
                        *ref_to_len_in_vm as usize
                    } else {
                        data.len()
                    },
                    syscall_parameter_address_restrictions,
                    virtual_address_space_adjustments,
                    account_data_direct_mapping,
                )?
            };
```

**File:** program-runtime/src/cpi.rs (L484-496)
```rust
        let vm_len_addr = vm_addr
            .saturating_add(&account_info.data_len as *const u64 as u64)
            .saturating_sub(account_info as *const _ as *const u64 as u64);
        if syscall_parameter_address_restrictions {
            // In the same vein as the other check_account_info_pointer() checks, we don't lock
            // this pointer to a specific address but we don't want it to be inside accounts, or
            // callees might be able to write to the pointed memory.
            if vm_len_addr >= solana_sbpf::ebpf::MM_INPUT_START {
                return Err(Box::new(CpiError::InvalidPointer));
            }
        }
        let ref_to_len_in_vm =
            translate_type_mut_for_cpi::<u64>(memory_mapping, vm_len_addr, false)?;
```

**File:** program-runtime/src/invoke_context.rs (L799-810)
```rust
    // Should alignment be enforced during user pointer translation
    pub fn get_check_aligned(&self) -> bool {
        self.transaction_context
            .get_current_instruction_context()
            .and_then(|instruction_context| {
                let owner_id = instruction_context.get_program_owner();
                debug_assert!(owner_id.is_ok());
                owner_id
            })
            .map(|owner_key| owner_key != bpf_loader_deprecated::id())
            .unwrap_or(true)
    }
```
