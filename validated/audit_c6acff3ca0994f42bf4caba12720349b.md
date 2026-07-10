### Title
Solana `FinalizeTransferPayload::serialize_for_near()` Omits `message` Field, Causing Permanent Fund Lock for Message-Bearing Transfers — (`solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs`)

---

### Summary

The Solana bridge's `FinalizeTransferPayload::serialize_for_near()` reconstructs the hash payload for MPC signature verification but omits the `message` field that NEAR includes when signing a `TransferMessagePayload` with a non-empty message. This serialization mismatch causes signature verification to always fail for any cross-chain transfer that carries a non-empty destination message to Solana, permanently locking the user's bridged funds.

---

### Finding Description

**NEAR signing side** (`near/omni-types/src/lib.rs`):

`TransferMessagePayload::encode_hashable()` conditionally includes the `message` field:

```rust
pub fn encode_hashable(&self) -> Result<Vec<u8>, String> {
    if self.message.is_empty() {
        borsh::to_vec(&TransferMessagePayloadV1::from(self.clone())).map_err(stringify)
    } else {
        borsh::to_vec(self).map_err(stringify)   // ← includes message bytes
    }
}
``` [1](#0-0) 

The full `TransferMessagePayload` struct includes `message: Vec<u8>`: [2](#0-1) 

When `sign_transfer` is called on NEAR, the `message` field is populated from the user's `msg` via `DestinationChainMsg::destination_msg()`: [3](#0-2) 

**Solana verification side** (`solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs`):

`FinalizeTransferPayload` has no `message` field:

```rust
pub struct FinalizeTransferPayload {
    pub destination_nonce: u64,
    pub transfer_id: TransferId,
    pub amount: u128,
    pub fee_recipient: Option<String>,
    // ← no message field
}
``` [4](#0-3) 

And `serialize_for_near()` stops after `fee_recipient` — no message bytes are appended:

```rust
// 6. fee_recipient
self.fee_recipient.serialize(&mut writer)?;
// ← message never written
``` [5](#0-4) 

**Signature verification** on Solana hashes only the truncated payload: [6](#0-5) 

When `message` is non-empty, NEAR's signed hash = `keccak256(prefix || nonce || transfer_id || token || amount || recipient || fee_recipient || message)`, while Solana's reconstructed hash = `keccak256(prefix || nonce || transfer_id || token || amount || recipient || fee_recipient)`. These are different hashes, so `secp256k1_recover` returns a different public key, and the `require!(signer.0 == *derived_near_bridge_address, ...)` check fails unconditionally.

---

### Impact Explanation

Any user who initiates a cross-chain transfer to Solana with a non-empty `message` payload will have their funds permanently locked. The transfer is recorded on NEAR (tokens deducted from the user), NEAR's MPC signer produces a valid signature over the full payload (including `message`), but Solana's `finalize_transfer` will always revert on signature verification because it reconstructs a shorter hash. There is no recovery path: the destination nonce is not marked used (the call reverts), but the source-chain tokens are already escrowed/burned. The funds are irrecoverably stuck.

This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

---

### Likelihood Explanation

Any unprivileged bridge user who calls `init_transfer` with a non-empty `message` field targeting a Solana recipient triggers this. The `message` field is a documented, user-controlled input to the bridge. No special privileges, key compromise, or colluding parties are required. The failure is deterministic and 100% reproducible for every such transfer.

---

### Recommendation

Add the `message` field to `FinalizeTransferPayload` and serialize it in `serialize_for_near()`, mirroring the NEAR `encode_hashable()` logic:

```rust
pub struct FinalizeTransferPayload {
    pub destination_nonce: u64,
    pub transfer_id: TransferId,
    pub amount: u128,
    pub fee_recipient: Option<String>,
    pub message: Vec<u8>,   // ← add this
}

// In serialize_for_near:
// 7. message (only if non-empty, matching NEAR's encode_hashable logic)
if !self.message.is_empty() {
    self.message.serialize(&mut writer)?;
}
```

This aligns Solana's hash reconstruction with NEAR's `TransferMessagePayload::encode_hashable()` for both the empty-message (V1) and non-empty-message (full) cases.

---

### Proof of Concept

1. User calls `init_transfer` on EVM/NEAR targeting a Solana recipient, with `message = b"some_payload"` (non-empty).
2. NEAR's `sign_transfer` builds `TransferMessagePayload { ..., message: b"some_payload" }` and calls `encode_hashable()`, which takes the `else` branch and borsh-serializes the full struct including the message bytes. MPC signs this hash.
3. Relayer submits the signed payload to Solana's `finalize_transfer`.
4. Solana's `FinalizeTransferPayload::serialize_for_near()` produces a byte sequence that ends after `fee_recipient` — the 12 bytes of `"some_payload"` (plus 4-byte length prefix) are absent.
5. `keccak::hash(&serialized)` on Solana ≠ `keccak256_array(encode_hashable())` on NEAR.
6. `secp256k1_recover` recovers a wrong public key; `require!(signer.0 == *derived_near_bridge_address)` panics.
7. Transaction reverts. The user's funds remain locked in the NEAR bridge with no finalization possible on Solana.

### Citations

**File:** near/omni-types/src/lib.rs (L670-692)
```rust
#[near(serializers=[borsh, json])]
#[derive(Debug, Clone)]
pub struct TransferMessagePayload {
    pub prefix: PayloadType,
    pub destination_nonce: Nonce,
    pub transfer_id: TransferId,
    pub token_address: OmniAddress,
    pub amount: U128,
    pub recipient: OmniAddress,
    pub fee_recipient: Option<AccountId>,
    #[serde(default)]
    pub message: Vec<u8>,
}

impl TransferMessagePayload {
    pub fn encode_hashable(&self) -> Result<Vec<u8>, String> {
        if self.message.is_empty() {
            borsh::to_vec(&TransferMessagePayloadV1::from(self.clone())).map_err(stringify)
        } else {
            borsh::to_vec(self).map_err(stringify)
        }
    }
}
```

**File:** near/omni-bridge/src/lib.rs (L487-506)
```rust
        let message = DestinationChainMsg::from_json(&transfer_message.msg)
            .and_then(|s| s.destination_msg())
            .unwrap_or_default();

        let transfer_payload = TransferMessagePayload {
            prefix: PayloadType::TransferMessage,
            destination_nonce: transfer_message.destination_nonce,
            transfer_id,
            token_address,
            amount: U128(amount_to_transfer),
            recipient: transfer_message.recipient,
            fee_recipient,
            message,
        };

        let payload = near_sdk::env::keccak256_array(
            transfer_payload
                .encode_hashable()
                .near_expect(BridgeError::Borsh),
        );
```

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L10-16)
```rust
#[derive(AnchorSerialize, AnchorDeserialize, Debug)]
pub struct FinalizeTransferPayload {
    pub destination_nonce: u64,
    pub transfer_id: TransferId,
    pub amount: u128,
    pub fee_recipient: Option<String>,
}
```

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L20-43)
```rust
    fn serialize_for_near(&self, params: Self::AdditionalParams) -> Result<Vec<u8>> {
        let mut writer = BufWriter::new(Vec::with_capacity(DEFAULT_SERIALIZER_CAPACITY));
        // 0. prefix
        IncomingMessageType::InitTransfer.serialize(&mut writer)?;
        // 1. destination_nonce
        self.destination_nonce.serialize(&mut writer)?;
        // 2. transfer_id
        writer.write_all(&[self.transfer_id.origin_chain])?;
        self.transfer_id.origin_nonce.serialize(&mut writer)?;
        // 3. token
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.0.serialize(&mut writer)?;
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. recipient
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.1.serialize(&mut writer)?;
        // 6. fee_recipient
        self.fee_recipient.serialize(&mut writer)?;

        writer
            .into_inner()
            .map_err(|_| error!(ErrorCode::InvalidArgs))
    }
```

**File:** solana/programs/bridge_token_factory/src/state/message/mod.rs (L23-47)
```rust
impl<P: Payload> SignedPayload<P> {
    pub fn verify_signature(
        &self,
        params: P::AdditionalParams,
        derived_near_bridge_address: &[u8; 64],
    ) -> Result<()> {
        let serialized = self.payload.serialize_for_near(params)?;
        let hash = keccak::hash(&serialized);

        let signature_bytes = &self.signature[0..64];

        let signature = libsecp256k1::Signature::parse_standard_slice(signature_bytes)
            .map_err(|_| ProgramError::InvalidArgument)?;
        require!(!signature.s.is_high(), ErrorCode::MalleableSignature);

        let signer = secp256k1_recover(&hash.to_bytes(), self.signature[64], signature_bytes)
            .map_err(|_| error!(ErrorCode::SignatureVerificationFailed))?;

        require!(
            signer.0 == *derived_near_bridge_address,
            ErrorCode::SignatureVerificationFailed
        );

        Ok(())
    }
```
