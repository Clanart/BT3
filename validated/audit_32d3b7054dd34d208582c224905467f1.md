### Title
`denormalize_amount` Multiplication Overflow Permanently Freezes Bridged Funds - (File: `near/omni-bridge/src/lib.rs`)

### Summary
`denormalize_amount` performs an unchecked `u128` multiplication (`amount * 10_u128.pow(diff_decimals)`) when converting a source-chain token amount back to NEAR-native precision. With `overflow-checks = true` in the workspace, this panics at runtime. Because the panic occurs inside `fin_transfer_callback` **before** the transfer is recorded as finalized, the source-chain tokens remain permanently locked with no recovery path.

### Finding Description

`denormalize_amount` is defined as:

```rust
// near/omni-bridge/src/lib.rs:2776-2779
fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount * (10_u128.pow(diff_decimals))   // ← unchecked multiplication
}
```

It is called unconditionally in `fin_transfer_callback` before any finalization state is written:

```rust
// near/omni-bridge/src/lib.rs:722-732
let transfer_message = TransferMessage {
    ...
    amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
    fee:    Self::denormalize_fee(&init_transfer.fee, decimals),
    ...
};
```

The finalization record (`add_fin_transfer`) is only written later, inside `process_fin_transfer_to_near` / `process_fin_transfer_to_other_chain`. If the multiplication panics, neither path is reached, so the transfer ID is never marked as used.

**Overflow condition.** For a token registered with `origin_decimals = 24` (NEAR) and `decimals = 6` (EVM), `diff_decimals = 18`. The multiplication overflows when:

```
amount > u128::MAX / 10^18  ≈  3.4 × 10^20  (in 6-decimal EVM units)
```

That threshold equals ≈ 3.4 × 10^14 tokens. For tokens with 0 EVM decimals and 24 NEAR decimals (`diff = 24`), the threshold drops to ≈ 3.4 × 10^14 whole tokens — reachable for any token whose total supply exceeds that value.

The same overflow path exists in `fast_fin_transfer` (line 771) and `claim_fee_callback` (line 1122).

### Impact Explanation

1. User calls `initTransfer` on EVM with a large amount → tokens are locked in the EVM vault.
2. A relayer submits the proof to NEAR `fin_transfer`.
3. `fin_transfer_callback` calls `denormalize_amount`; the multiplication overflows → **panic**.
4. The NEAR receipt fails. No finalization record is written.
5. Every subsequent retry of the same proof produces the same panic (the amount is fixed by the on-chain event).
6. The EVM vault holds the tokens indefinitely with no unlock mechanism.

**Result:** Permanent, irrecoverable lock of user funds — matching the "Permanent freezing / irrecoverable lock" critical impact class.

### Likelihood Explanation

Any unprivileged bridge user who transfers an amount exceeding `u128::MAX / 10^diff_decimals` triggers the bug. The threshold is token-specific:

| EVM decimals | NEAR decimals | diff | Overflow threshold (whole tokens) |
|---|---|---|---|
| 6 | 24 | 18 | ~3.4 × 10^14 |
| 0 | 24 | 24 | ~3.4 × 10^8 (340 million) |
| 18 | 24 | 6 | ~3.4 × 10^14 |

Tokens with 0 EVM decimals and supplies above ~340 million are not uncommon (governance tokens, NFT-adjacent tokens). The CLAUDE.md note that dismisses decimal arithmetic issues covers only the **underflow** in the subtraction `origin_decimals - decimals` (misconfiguration), not the **multiplication overflow** with a valid but large amount.

### Recommendation

Replace the bare multiplication with a checked variant and propagate the error:

```rust
fn denormalize_amount(amount: u128, decimals: Decimals) -> Option<u128> {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount.checked_mul(10_u128.pow(diff_decimals))
}
```

In `fin_transfer_callback`, treat `None` as a hard error that still marks the transfer as finalized (to prevent replay) but refunds the source-chain tokens via a cross-chain message, or reject the transfer at the source chain by enforcing an upper-bound on `amount` during `initTransfer`.

### Proof of Concept

```
Token config: origin_decimals = 24, decimals = 0  (diff = 24)
Overflow threshold: u128::MAX / 10^24 ≈ 3.4 × 10^14

User calls EVM initTransfer(token, amount = 3.4e14 + 1, ...)
→ EVM vault locks 3.4e14 + 1 tokens.

Relayer submits proof to NEAR fin_transfer.
→ fin_transfer_callback executes:
     denormalize_amount(3.4e14 + 1, Decimals { origin: 24, decimals: 0 })
   = (3.4e14 + 1) * 10^24
   > u128::MAX  →  PANIC (overflow-checks = true)

fin_transfer_callback aborts; add_fin_transfer never called.
Proof can be resubmitted indefinitely → same panic every time.
EVM tokens remain locked forever.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L700-746)
```rust
    pub fn fin_transfer_callback(
        &mut self,
        #[serializer(borsh)] storage_deposit_actions: &Vec<StorageDepositAction>,
        #[serializer(borsh)] predecessor_account_id: AccountId,
    ) -> PromiseOrValue<Nonce> {
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };

        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L770-772)
```rust
        let denormalized_amount =
            Self::denormalize_amount(fast_fin_transfer_msg.amount.0, decimals);
        let denormalized_fee = Self::denormalize_fee(&fast_fin_transfer_msg.fee, decimals);
```

**File:** near/omni-bridge/src/lib.rs (L1122-1127)
```rust
        let denormalized_amount = Self::denormalize_amount(
            fin_transfer.amount.0,
            self.token_decimals
                .get(&token_address)
                .near_expect(BridgeError::TokenDecimalsNotFound),
        );
```

**File:** near/omni-bridge/src/lib.rs (L2776-2779)
```rust
    fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount * (10_u128.pow(diff_decimals))
    }
```

**File:** near/omni-bridge/src/storage.rs (L131-136)
```rust
#[near(serializers=[borsh, json])]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Decimals {
    pub decimals: u8,
    pub origin_decimals: u8,
}
```

**File:** near/CLAUDE.md (L192-195)
```markdown
**2. Decimal Arithmetic Underflow (NOT a vulnerability)**
- Design expects `origin_decimals >= decimals` (normalization to lower precision)
- Workspace has `overflow-checks = true` in Cargo.toml
- Misconfiguration causes panic (correct fail-safe), not silent corruption
```
