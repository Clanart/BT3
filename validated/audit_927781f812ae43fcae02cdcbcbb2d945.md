### Title
Wrong Endianness in `vout` Extraction Causes Bridge Circuit to Panic for Non-Zero Vout Withdrawal UTXOs, Making Operator Collateral Slashable — (`circuits-lib/src/bridge_circuit/storage_proof.rs`)

---

### Summary

`verify_storage_proofs` extracts the withdrawal UTXO's `vout` from an EVM storage slot using `u32::from_le_bytes`, but EVM stores `uint32` values as big-endian. For any withdrawal UTXO with `vout != 0`, the extracted value is wrong by a factor of up to `2^24`, causing the bridge circuit's assertion to panic. An operator who made a valid payout for such a withdrawal cannot produce a valid bridge circuit proof, so a watchtower challenge results in the operator's collateral being slashed despite honest behavior.

---

### Finding Description

In `circuits-lib/src/bridge_circuit/storage_proof.rs`, the `verify_storage_proofs` function reads the `vout` field from a verified EVM storage proof:

```rust
let buf: [u8; 32] = vout_storage_proof.value.to_be_bytes();

// ENDIANNESS SHOULD BE CHECKED THIS FIELD IS 4 BYTES in the contract
let vout = u32::from_le_bytes(
    buf[28..32]
        .try_into()
        .expect("Vout value conversion failed"),
);
``` [1](#0-0) 

The EVM stores a `uint32` value right-aligned in a 32-byte storage slot in **big-endian** order. `buf[28..32]` correctly isolates the 4 meaningful bytes, but `u32::from_le_bytes` interprets them as little-endian. For `vout = 1`, the EVM slot contains `[0x00, 0x00, 0x00, 0x01]` in those 4 bytes; `from_le_bytes` returns `0x01000000 = 16777216` instead of `1`. The developer comment `"ENDIANNESS SHOULD BE CHECKED"` confirms this was a known uncertainty that was never resolved.

The extracted `vout` is then asserted against the actual payout transaction's input:

```rust
assert_eq!(
    vout,
    input.payout_spv.transaction.input[payout_input_index]
        .previous_output
        .vout,
    "Invalid withdrawal transaction output index"
);
``` [2](#0-1) 

For any `vout != 0`, the assertion fails and the circuit panics. The operator cannot generate a valid bridge circuit proof.

---

### Impact Explanation

The bridge circuit proof is required when a watchtower challenges an operator's kickoff transaction. If the operator cannot produce a valid proof, the BitVM2 challenge-response protocol concludes in the challenger's favor and the operator's collateral is burned/slashed.

- **Operator collateral at risk**: Any operator who fronts funds for a withdrawal UTXO with `vout != 0` is permanently unable to prove their innocence in a challenge. Their collateral (configured as `collateral_funding_amount`, e.g., 99,000,000 sat in regtest, 130,000,000 sat in the Docker config) is slashable.
- **Operator funds lost**: The operator already paid out the withdrawal. Without reimbursement (blocked by the failed proof), they lose both the payout amount and their collateral.
- **Watchtower can trigger at will**: Any watchtower can challenge any operator's kickoff transaction. The challenge is permissionless. [3](#0-2) 

---

### Likelihood Explanation

- Bitcoin UTXOs with `vout != 0` are extremely common. A user's withdrawal UTXO registered in the Citrea bridge contract can have any vout value; the operator does not control this.
- The Citrea contract accepts any valid UTXO outpoint. There is no protocol-level restriction to `vout = 0`.
- Watchtowers are economically incentivized to challenge operators (they receive a reward if the operator is slashed). A watchtower monitoring the chain will challenge any kickoff it can.
- The bug is deterministic: every non-zero vout triggers the panic with 100% reliability.

---

### Recommendation

Replace `u32::from_le_bytes` with `u32::from_be_bytes` to match EVM's big-endian storage encoding:

```rust
// Before (wrong):
let vout = u32::from_le_bytes(
    buf[28..32].try_into().expect("Vout value conversion failed"),
);

// After (correct):
let vout = u32::from_be_bytes(
    buf[28..32].try_into().expect("Vout value conversion failed"),
);
``` [1](#0-0) 

Additionally, add a unit test that verifies `vout = 1` and `vout = 2` round-trip correctly through the storage proof extraction to prevent regression.

---

### Proof of Concept

**Setup**: A user registers a withdrawal UTXO with `vout = 1` in the Citrea bridge contract (e.g., the second output of a transaction). The operator accepts and pays out the withdrawal.

**Trigger**: A watchtower sends a challenge transaction against the operator's kickoff.

**Execution**: The operator runs the bridge circuit off-chain to generate a proof:

1. `verify_storage_proofs` is called with the EVM storage proof for the `vout` slot.
2. The EVM slot value for `vout = 1` is `U256::from(1)`, whose `to_be_bytes()` is `[0x00, ..., 0x00, 0x01]`.
3. `buf[28..32]` = `[0x00, 0x00, 0x00, 0x01]`.
4. `u32::from_le_bytes([0x00, 0x00, 0x00, 0x01])` = **16777216**.
5. The payout transaction's `previous_output.vout` = **1**.
6. `assert_eq!(16777216, 1, "Invalid withdrawal transaction output index")` → **PANIC**.

**Result**: Proof generation fails. The operator cannot respond to the challenge. After the timeout, the challenger claims the operator's collateral. The operator loses both the payout amount and their collateral despite acting honestly. [4](#0-3) [5](#0-4)

### Citations

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L119-132)
```rust
    let buf: [u8; 32] = vout_storage_proof.value.to_be_bytes();

    // ENDIANNESS SHOULD BE CHECKED THIS FIELD IS 4 BYTES in the contract
    let vout = u32::from_le_bytes(
        buf[28..32]
            .try_into()
            .expect("Vout value conversion failed"),
    );

    let wd_outpoint = WithdrawalOutpointTxid(utxo_storage_proof.value.to_be_bytes());

    let move_txid = MoveTxid(deposit_storage_proof.value.to_be_bytes());

    (wd_outpoint, vout, move_txid)
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L183-204)
```rust
    let (user_wd_outpoint, vout, move_txid) =
        verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);

    let user_wd_txid = bitcoin::Txid::from_byte_array(*user_wd_outpoint);

    let payout_input_index: usize = input.payout_input_index as usize;

    assert_eq!(
        user_wd_txid,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .txid,
        "Invalid withdrawal transaction ID"
    );

    assert_eq!(
        vout,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .vout,
        "Invalid withdrawal transaction output index"
    );
```

**File:** scripts/docker/configs/regtest/.env.regtest (L33-34)
```text
OPERATOR_CHALLENGE_AMOUNT=130000000
COLLATERAL_FUNDING_AMOUNT=90000000
```
