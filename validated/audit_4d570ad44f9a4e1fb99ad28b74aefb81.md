### Title
Unchecked timestamp subtraction in Bitcoin difficulty-adjustment causes wrong-direction retarget on underflow - (File: circuits-lib/src/header_chain/mod.rs)

### Summary
`calculate_new_difficulty` computes the actual epoch timespan with plain unsigned subtraction instead of a checked/signed calculation. If `last_timestamp` is smaller than `epoch_start_time` (a case the timestamp-median rule does not exclude), the subtraction underflows `u32` and wraps to a value near `u32::MAX`, which then falls into the *upper* clamp branch (`EXPECTED_EPOCH_TIMESPAN * 4`) instead of the *lower* clamp branch that real Bitcoin difficulty logic would select for a negative timespan. This flips the retarget outcome from "difficulty should stay roughly the same/slightly harder" to "difficulty becomes 4x easier," directly weakening the proof-of-work target that the header-chain circuit (and hence the bridge circuit's total-work comparison) relies on.

### Finding Description
`calculate_new_difficulty` is: [1](#0-0) 

The line [2](#0-1) 
performs `last_timestamp - epoch_start_time` with no `checked_sub`/signed arithmetic, unlike the rest of the codebase which consistently uses `checked_add`/`checked_sub`/`wrapping_*` for security-relevant arithmetic (e.g. [3](#0-2)  and [4](#0-3) ). `epoch_start_time` and `last_timestamp` are both attacker-influenced block-header timestamps validated only by `validate_timestamp`, which only enforces `block.time > median(prev 11 timestamps)`: [5](#0-4) 
This local median check does not prevent a block ~2016 heights later from having a timestamp lower than the very first block of its own epoch, so `last_timestamp < epoch_start_time` is reachable while still satisfying every other consensus check performed in `apply_block_headers`.

When that underflow happens, `actual_timespan` wraps to a value close to `u32::MAX`, which is always `> EXPECTED_EPOCH_TIMESPAN * 4`, forcing the code into the branch that clamps to the *maximum allowed increase* (`EXPECTED_EPOCH_TIMESPAN * 4`), i.e. the *easiest* possible new target: [6](#0-5) 
This is the opposite of Bitcoin Core's own logic, where a negative timespan is clamped to the minimum bound (`EXPECTED_EPOCH_TIMESPAN / 4`), keeping the target essentially unchanged. Here the bug makes the chain's difficulty 4x easier than intended for the next epoch.

The header-chain circuit's output (`total_work`) feeds directly into the bridge circuit's core security invariant that the operator's claimed chain must have *more* cumulative proof-of-work than any watchtower's Work-Only proof: [7](#0-6) 
By manipulating the retarget direction through this underflow, headers that required substantially less real hashing power can be assigned inflated `total_work`, breaking the binding "a block hash committed versus a block hash proved" — the circuit can be made to accept/prefer a chain that does not actually represent the claimed amount of work.

### Impact Explanation
If exploitable at proof-generation time (in a network/epoch length where an attacker fully controls header submission, e.g. any network using this shared circuit code such as regtest/signet/testnet4 configurations of the protocol, or via constructing an alternate low-work chain segment during normal operation), this could let a party present a "false circuit claim" of sufficient chain work without doing the corresponding work, which per the scope rules is a Critical impact ("a false circuit claim proved or a true one made unprovable").

### Likelihood Explanation
Exploitability requires crafting a sequence of ~2016 block headers where the last header's timestamp is lower than the epoch's first header's timestamp while satisfying the local median-of-11 timestamp constraint and the proof-of-work-per-block constraint at each step — this is a non-trivial but not obviously infeasible sequence to construct off-chain (the headers themselves need not be real-network-mined chains for a first party proving `header_chain_circuit` from a fresh genesis, since the circuit only checks its own internal consistency, not against the real Bitcoin network). I was not able to fully verify from the code alone whether an additional external anchor (e.g., a required match against known genesis hash checkpoints tied to the real Bitcoin chain) closes this path, which would need to be checked against `chain_state`/genesis initialization logic outside the excerpts reviewed.

### Recommendation
Replace the raw subtraction with `last_timestamp.checked_sub(epoch_start_time)`, and on `None` (i.e., timestamp went backwards across the epoch), clamp `actual_timespan` to the *minimum* allowed value (`EXPECTED_EPOCH_TIMESPAN / 4`), mirroring how a negative timespan is handled in Bitcoin Core's signed-integer implementation, instead of allowing it to wrap and hit the maximum-increase branch.

### Proof of Concept
Conceptual PoC (not executed):
1. Construct a sequence of headers for one difficulty epoch where each header's `time` satisfies `time > median(prev 11 times)` but the overall trend decreases the timestamp below the epoch's first block time by the time the epoch's last block is reached (e.g., alternate small increases and larger permissible dips relative to a rolling 11-block median).
2. Feed this sequence into `header_chain_circuit`/`apply_block_headers`; at the epoch boundary, `calculate_new_difficulty(epoch_start_time, last_timestamp, current_target_bits)` is invoked with `last_timestamp < epoch_start_time`.
3. Observe (in an unchecked/release build without overflow panics) that `actual_timespan` wraps near `u32::MAX`, forcing the "> EXPECTED_EPOCH_TIMESPAN * 4" branch and producing a target 4x larger (mining 4x easier) than the correct, intended clamp of `EXPECTED_EPOCH_TIMESPAN / 4`.
4. Continue producing headers under this artificially eased target, accumulating `total_work` values in the resulting `BlockHeaderCircuitOutput` that overstate real computational effort, then supply this HCP to `bridge_circuit` where it is compared against watchtower `TotalWork` at [8](#0-7) .

### Citations

**File:** circuits-lib/src/header_chain/mod.rs (L481-483)
```rust
            if !validate_timestamp(block_header.time, self.prev_11_timestamps) {
                panic!("Timestamp is not valid, it must be greater than the median of the last 11 timestamps");
            }
```

**File:** circuits-lib/src/header_chain/mod.rs (L635-656)
```rust
fn calculate_new_difficulty(
    epoch_start_time: u32,
    last_timestamp: u32,
    current_target: u32,
) -> [u8; 32] {
    let mut actual_timespan = last_timestamp - epoch_start_time;
    if actual_timespan < EXPECTED_EPOCH_TIMESPAN / 4 {
        actual_timespan = EXPECTED_EPOCH_TIMESPAN / 4;
    } else if actual_timespan > EXPECTED_EPOCH_TIMESPAN * 4 {
        actual_timespan = EXPECTED_EPOCH_TIMESPAN * 4;
    }

    let current_target_bytes = bits_to_target(current_target);
    let mut new_target = U256::from_be_bytes(current_target_bytes)
        .wrapping_mul(&U256::from(actual_timespan))
        .wrapping_div(&U256::from(EXPECTED_EPOCH_TIMESPAN));

    if new_target > NETWORK_CONSTANTS.max_target {
        new_target = NETWORK_CONSTANTS.max_target;
    }
    new_target.to_be_bytes()
}
```

**File:** core/src/operator.rs (L509-522)
```rust
        // Use checked_sub to safely handle potential underflow
        let withdrawal_diff = match withdrawal_amount
            .to_sat()
            .checked_sub(input_amount.to_sat())
        {
            Some(diff) => Amount::from_sat(diff),
            None => {
                // input amount is greater than withdrawal amount, so it's profitable but doesn't make sense
                tracing::warn!(
                    "Some user gave more amount than the withdrawal amount as input for withdrawal"
                );
                return true;
            }
        };
```

**File:** core/src/builder/transaction/operator_collateral.rs (L399-403)
```rust
    for &idx in unused_kickoff_connectors_indices {
        let txin = round_txhandler.get_spendable_output(UtxoVout::Kickoff(idx))?;
        input_amount = input_amount.checked_add(txin.get_prevout().value).ok_or(
            BridgeError::ArithmeticOverflow("Amount overflow in burn unused kickoff connectors tx"),
        )?;
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L148-160)
```rust
    let (max_total_work, challenge_sending_watchtowers) =
        total_work_and_watchtower_flags(&input, &work_only_image_id);

    let total_work: TotalWork = input.hcp.chain_state.total_work[16..32]
        .try_into()
        .expect("Cannot fail");

    // If total work is less than the max total work of watchtowers, panic
    if total_work < max_total_work {
        panic!(
            "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
        );
    }
```
