### Title
Source-Chain Accepts `uint128` Transfer Amounts to Solana But Solana Finalizer Truncates to `u64`, Permanently Locking Funds — (`solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs`, `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The EVM `OmniBridge.initTransfer` and NEAR bridge accept transfer amounts typed as `uint128`/`u128` with no upper-bound check for Solana compatibility. When such a transfer is routed to Solana, `finalize_transfer` attempts a checked narrowing cast from `u128` to `u64`. If the amount exceeds `u64::MAX` (≈ 1.84 × 10¹⁹), the cast fails with `AmountOverflow`, the transaction reverts, and the nonce is never consumed. Because the source-chain tokens are already burned or locked and no refund path exists, the funds are permanently irrecoverable.

---

### Finding Description

**EVM entry point — no Solana-range guard:**

`OmniBridge.initTransfer` accepts `uint128 amount`. The Solidity type `uint128` has a maximum of 2¹²⁸ − 1, far above Solana's SPL token ceiling of `u64::MAX` (2⁶⁴ − 1). No check is performed to ensure the amount is within the Solana-compatible range before tokens are burned or locked. [1](#0-0) 

**Solana finalizer — checked cast that always reverts for oversized amounts:**

In `finalize_transfer.rs`, both the native-token (`transfer_checked`) and bridged-token (`mint_to`) paths perform:

```rust
data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?
```

This is a `u128 → u64` narrowing conversion. Any amount above `u64::MAX` returns `AmountOverflow` and reverts the entire transaction. [2](#0-1) [3](#0-2) 

The same pattern exists for SOL transfers: [4](#0-3) 

**Nonce never consumed on revert:**

`UsedNonces::use_nonce` is called at the top of `process`. Because Solana transactions are atomic, a revert at the `try_into` step rolls back the nonce write. The nonce is therefore never marked used, so the relayer can retry — but every retry will fail identically. The transfer is permanently stuck. [5](#0-4) 

**Protocol-confirmed behavior — test exists for this exact overflow:**

The test suite explicitly verifies that `amount = u64::MAX + 1` causes `AmountOverflow` (error code 6010), confirming the revert path is real and not a theoretical edge case. [6](#0-5) 

---

### Impact Explanation

**Critical — Permanent, irrecoverable lock of bridged user funds.**

Once `initTransfer` executes on EVM (or the equivalent NEAR call), the user's tokens are burned or transferred into the bridge vault. The cross-chain message is committed. If the Solana finalization always reverts, there is no on-chain refund path visible in the codebase: `pending_transfers` on NEAR records the transfer but no cancellation/refund function exists for the Solana destination leg. The user loses their entire bridged amount with no recourse.

---

### Likelihood Explanation

**Medium-to-High**, depending on token decimals:

- For tokens where the NEAR bridge applies no decimal normalization (i.e., `origin_decimals == solana_decimals`), the amount in `FinalizeTransferPayload` equals the raw user-supplied amount. `u64::MAX` with 18 decimals is only ≈ 18.4 tokens — easily exceeded by any user bridging a modest amount of a high-precision token (e.g., a WETH-equivalent).
- Even with normalization (`normalize_amount` divides by `10^(origin_decimals − dest_decimals)`), if the difference is small (e.g., 18 → 15 decimals), the threshold is still ≈ 18,400 tokens, reachable by a whale.
- The EVM interface imposes no guard, so any unprivileged user can trigger this by simply calling `initTransfer` with a large amount destined for Solana. [7](#0-6) 

---

### Recommendation

1. **Source-chain guard**: In `OmniBridge.initTransfer` (and the NEAR equivalent), when the destination chain is Solana, validate `amount <= type(uint64).max` before burning/locking tokens.
2. **Payload-level guard**: In the NEAR bridge, before constructing the Wormhole message to Solana, assert `normalized_amount <= u64::MAX` and revert/refund if not.
3. **Refund path**: Implement a cancellation/refund mechanism for transfers whose Solana finalization has permanently failed, so funds do not remain locked in the bridge.

---

### Proof of Concept

1. User calls `OmniBridge.initTransfer` on EVM with `amount = uint128(uint64(type(uint64).max)) + 1` (i.e., `2^64`) and `recipient = "<solana_address>"`. The EVM contract accepts this, burns the tokens, and emits `InitTransfer`.
2. The cross-chain message is relayed through NEAR to Solana. The `FinalizeTransferPayload.amount` field is `2^64` (a valid `u128`).
3. The relayer submits `finalize_transfer` on Solana. Execution reaches:
   ```rust
   data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?
   // 2^64 does not fit in u64 → AmountOverflow (error 6010)
   ```
4. The transaction reverts. The nonce is not consumed. Every subsequent retry fails identically.
5. The user's tokens are permanently locked — burned on EVM, undeliverable on Solana, with no refund mechanism.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-384)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
        if (fee >= amount) {
            revert InvalidFee();
        }
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L91-99)
```rust
        UsedNonces::use_nonce(
            data.destination_nonce,
            &self.used_nonces,
            &mut self.config,
            self.authority.to_account_info(),
            self.common.payer.to_account_info(),
            &Rent::get()?,
            self.system_program.to_account_info(),
        )?;
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L114-114)
```rust
                data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L134-134)
```rust
                data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L88-88)
```rust
            data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
```

**File:** solana/programs/bridge_token_factory/tests/mollusk/test_finalize_transfer.rs (L261-270)
```rust
fn finalize_transfer_amount_overflow() {
    let result = run_finalize_transfer(TestParams {
        amount: u128::from(u64::MAX) + 1,
        ..Default::default()
    });

    assert_eq!(
        result.program_result,
        ProgramResult::Failure(ProgramError::Custom(6010))
    );
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
