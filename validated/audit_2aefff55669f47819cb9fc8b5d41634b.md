[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** program-runtime/src/cpi.rs (L14-14)
```rust
    solana_pubkey::{MAX_SEEDS, Pubkey, PubkeyError},
```

**File:** program-runtime/src/cpi.rs (L652-660)
```rust
                let untranslated_seeds = translate_slice::<VmSlice<u8>>(
                    memory_mapping,
                    signer_seeds.ptr(),
                    signer_seeds.len(),
                    check_aligned,
                )?;
                if untranslated_seeds.len() > MAX_SEEDS {
                    return Err(Box::new(InstructionError::MaxSeedLengthExceeded) as Error);
                }
```

**File:** program-runtime/src/cpi.rs (L667-668)
```rust
                Pubkey::create_program_address(&seeds_bytes, program_id)
                    .map_err(|err| Box::new(CpiError::BadSeeds(err)) as Error)
```

**File:** program-runtime/src/memory.rs (L94-123)
```rust
    ($memory_mapping:expr, AccessType::Load, $vm_addr:expr, $len:expr, $T:ty, $check_aligned:expr $(,)?) => {{
        if $len == 0 {
            Ok(std::ptr::slice_from_raw_parts(
                std::ptr::dangling_mut::<$T>(),
                0,
            ))
        } else {
            let total_size = $len.saturating_mul(size_of::<$T>() as u64);
            if isize::try_from(total_size).is_err() {
                Err($crate::memory::MemoryTranslationError::InvalidLength.into())
            } else {
                match $crate::translate_inner!(
                    $memory_mapping,
                    map,
                    $crate::solana_sbpf::memory_region::AccessType::Load,
                    $vm_addr,
                    total_size
                ) {
                    Err(e) => Err(e),
                    Ok(host_buf) if $check_aligned && !host_buf.ptr().cast::<$T>().is_aligned() => {
                        Err($crate::memory::MemoryTranslationError::UnalignedPointer.into())
                    }
                    Ok(host_buf) => Ok(std::ptr::slice_from_raw_parts(
                        host_buf.ptr().cast(),
                        $len as usize,
                    )),
                }
            }
        }
    }};
```

**File:** program-runtime/src/memory.rs (L195-201)
```rust
pub fn translate_vm_slice<'a, T>(
    slice: &VmSlice<T>,
    memory_mapping: &'a MemoryMapping,
    check_aligned: bool,
) -> Result<&'a [T], Box<dyn std::error::Error>> {
    translate_slice::<T>(memory_mapping, slice.ptr(), slice.len(), check_aligned)
}
```
