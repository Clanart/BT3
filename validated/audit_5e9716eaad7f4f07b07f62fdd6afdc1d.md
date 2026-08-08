### No Vulnerability found for this question.

**Reasoning:** The compute-cost model for Poseidon is defined by `SVMTransactionExecutionCost::poseidon_cost`, which computes `61*n^2 + 542` purely from `nr_inputs` (`vals_len`), independent of per-input byte length [1](#0-0) . The syscall enforces `vals_len <= 12`, charges this cost via `compute_meter.consume_checked`, and then maps each `VmSlice<u8>` input via `translate_vm_slice` before calling `poseidon::hashv` from the external `solana_poseidon` crate [2](#0-1) .

However, the actual Poseidon hash algorithm and its input validation (including any per-input byte-size constraints tied to the BN254 field element size) live entirely inside the `solana_poseidon` crate, which is imported as `solana_poseidon as poseidon` [3](#0-2) . This crate is a dependency external to this repository, and per the audit rules, "dependencies... [are] out of scope." Determining whether `poseidon::hashv` enforces a bound on per-input byte length (which would refute the premise that inputs are "unrestricted in size aside from mapped memory bounds") requires inspecting that dependency's source, which cannot be validated within scope here.

Since the root of the claimed underpricing (whether wall-clock cost genuinely scales unboundedly with per-input byte length, or whether the dependency already caps/rejects oversized field elements) is not resolvable from in-scope files, and the rules explicitly exclude dependency behavior from the audit, this cannot be confirmed as a valid, in-scope finding.

### Citations

**File:** program-runtime/src/execution_budget.rs (L265-293)
```rust
impl SVMTransactionExecutionCost {
    /// Returns cost of the Poseidon hash function for the given number of
    /// inputs is determined by the following quadratic function:
    ///
    /// 61*n^2 + 542
    ///
    /// Which approximates the results of benchmarks of light-posiedon
    /// library[0]. These results assume 1 CU per 33 ns. Examples:
    ///
    /// * 1 input
    ///   * light-poseidon benchmark: `18,303 / 33 ≈ 555`
    ///   * function: `61*1^2 + 542 = 603`
    /// * 2 inputs
    ///   * light-poseidon benchmark: `25,866 / 33 ≈ 784`
    ///   * function: `61*2^2 + 542 = 786`
    /// * 3 inputs
    ///   * light-poseidon benchmark: `37,549 / 33 ≈ 1,138`
    ///   * function; `61*3^2 + 542 = 1091`
    ///
    /// [0] https://github.com/Lightprotocol/light-poseidon#performance
    pub fn poseidon_cost(&self, nr_inputs: u64) -> Option<u64> {
        let squared_inputs = nr_inputs.checked_pow(2)?;
        let mul_result = self
            .poseidon_cost_coefficient_a
            .checked_mul(squared_inputs)?;
        let final_result = mul_result.checked_add(self.poseidon_cost_coefficient_c)?;

        Some(final_result)
    }
```

**File:** syscalls/src/lib.rs (L25-25)
```rust
    solana_keccak_hasher as keccak, solana_poseidon as poseidon,
```

**File:** syscalls/src/lib.rs (L2464-2506)
```rust
        let parameters: poseidon::Parameters = parameters.try_into()?;
        let endianness: poseidon::Endianness = endianness.try_into()?;

        if vals_len > 12 {
            ic_msg!(
                invoke_context,
                "Poseidon hashing {} sequences is not supported",
                vals_len,
            );
            return Err(SyscallError::InvalidLength.into());
        }

        let execution_cost = invoke_context.get_execution_cost();
        let Some(cost) = execution_cost.poseidon_cost(vals_len) else {
            ic_msg!(
                invoke_context,
                "Overflow while calculating the compute cost"
            );
            return Err(SyscallError::ArithmeticOverflow.into());
        };
        invoke_context
            .compute_meter
            .consume_checked(cost.to_owned())?;

        let check_aligned = invoke_context.get_check_aligned();
        let memory_mapping = invoke_context.memory_contexts.memory_mapping_mut()?;
        {
            // Just a check that this will map later for error compatibility with old code.
            translate_mut!(
                memory_mapping,
                check_aligned,
                let _result: (&mut [MaybeUninit<u8>]) =
                    map(result_addr, poseidon::HASH_BYTES as u64)?;
            );
        }
        let inputs =
            translate_slice::<VmSlice<u8>>(memory_mapping, vals_addr, vals_len, check_aligned)?;
        let inputs = inputs
            .iter()
            .map(|input| translate_vm_slice(input, memory_mapping, check_aligned))
            .collect::<Result<Vec<_>, Error>>()?;

        let result = poseidon::hashv(parameters, endianness, inputs.as_slice());
```
