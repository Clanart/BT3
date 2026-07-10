### Title
Serialization Schema Mismatch Between NEAR `TransferMessagePayload::encode_hashable()` and Solana `FinalizeTransferPayload::serialize_for_near()` Permanently Locks Funds on Solana-Bound Transfers With Non-Empty `DestHexMsg` — (File: `near/omni-types/src/lib.rs`, `solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs`)

---

### Summary

NEAR's `sign_transfer` conditionally serializes `TransferMessagePayload` in a V2 layout (appending the `message: Vec<u8>` field) whenever `message` is non-empty. Solana's `FinalizeTransferPayload::serialize_for_near()` always produces the V1 layout (no `message` field). Any user who initiates a NEAR→Solana transfer with `msg: {"DestHexMsg": "..."}` causes NEAR to sign a V2 hash while Solana reconstructs a V1 hash, making signature verification permanently fail and irrecoverably locking the user's bridged tokens.

---

### Finding Description

**NEAR side — conditional V1/V2 serialization:**

`TransferMessagePayload::encode_hashable()` in `near/omni-types/src/lib.rs` chooses between two distinct Borsh layouts:

```rust
pub fn encode_hashable(&self) -> Result<Vec<u8>, String> {
    if self.message.is_empty() {
        borsh::to_vec(&TransferMessagePayloadV1::from(self.clone())).map_err(stringify)
    } else {
        borsh::to_vec(self).map_err(stringify)   // V2: includes `message: Vec<u8>`
    }
}
``` [1](#0-0) 

`TransferMessagePayloadV1` omits the `message` field entirely; `TransferMessagePayload` (V2) appends it as a Borsh `Vec<u8>` (4-byte LE length prefix + raw bytes). [2](#0-1) 

The `message` field is populated in `sign_transfer` from the user-supplied `msg` string via `DestinationChainMsg::DestHexMsg`:

```rust
let message = DestinationChainMsg::from_json(&transfer_message.msg)
    .and_then(|s| s.destination_msg())
    .unwrap_or_default();
``` [3](#0-2) 

There is no chain-specific guard: if the destination is Solana and `message` is non-empty, NEAR still signs the V2 layout.

**Solana side — always V1 layout:**

`FinalizeTransferPayload::serialize_for_near()` in `solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs` serializes:

```
IncomingMessageType::InitTransfer | destination_nonce | origin_chain | origin_nonce
| SOLANA_CHAIN_ID + mint | amount | SOLANA_CHAIN_ID + recipient | fee_recipient
```

No `message` field is ever written. [4](#0-3) 

`SignedPayload::verify_signature` hashes exactly this output and checks it against the MPC signature:

```rust
let serialized = self.payload.serialize_for_near(params)?;
let hash = keccak::hash(&serialized);
``` [5](#0-4) 

**The mismatch:** When `message` is non-empty, NEAR signs `keccak(V2_bytes)` but Solana verifies against `keccak(V1_bytes)`. The two hashes differ by exactly the 4-byte length prefix plus the message bytes appended in V2. Signature verification always fails.

**User entry path:** `InitTransferMsg.msg` is an unconstrained `Option<BoundedString<2048>>` accepted from any caller of `ft_on_transfer`. Storing `{"DestHexMsg":"<hex>"}` is sufficient to arm the mismatch. [6](#0-5) 

There is no cancel/refund path for a stuck `pending_transfer` entry on NEAR, so the locked tokens are irrecoverable.

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

Once the transfer is stored in `pending_transfers` and `sign_transfer` produces a V2-signed payload, no Solana instruction can ever finalize it (signature always fails). The user's tokens remain locked in the NEAR bridge contract indefinitely with no on-chain escape hatch.

---

### Likelihood Explanation

Any unprivileged bridge user can trigger this. The `msg` field is a standard, documented parameter of `InitTransferMsg`. A user who reads the `DestHexMsg` variant (e.g., to attach a destination-chain hook payload) and sends a transfer to a Solana address will unknowingly lock their funds. No special role, key, or colluding party is required.

---

### Recommendation

1. **Immediate guard in `sign_transfer`:** Reject or strip `message` when the destination chain is a Solana-variant (`ChainKind::Sol` / `ChainKind::Fogo`) before constructing `TransferMessagePayload`, so `encode_hashable()` always falls back to V1 for those chains.
2. **Long-term fix:** Update `FinalizeTransferPayload::serialize_for_near()` to include the `message` field (matching V2 layout) and update the Solana `FinalizeTransferPayload` struct accordingly, so the two sides stay in sync when the message feature is extended to Solana.
3. **Invariant test:** Add a cross-chain serialization round-trip test that asserts `NEAR::encode_hashable(payload_with_message) == Solana::serialize_for_near(equivalent_payload)`.

---

### Proof of Concept

1. User calls `ft_on_transfer` on NEAR with:
   ```json
   {
     "InitTransfer": {
       "recipient": "sol:<base58_pubkey>",
       "fee": "0",
       "native_token_fee": "0",
       "msg": "{\"DestHexMsg\":\"deadbeef\"}"
     }
   }
   ```
2. NEAR stores `transfer_message.msg = "{\"DestHexMsg\":\"deadbeef\"}"`.
3. Relayer calls `sign_transfer(transfer_id, ...)`.
4. `DestinationChainMsg::from_json` parses `DestHexMsg` → `message = [0xde, 0xad, 0xbe, 0xef]`.
5. `encode_hashable()` branches to V2 (message non-empty) → Borsh bytes include `04 00 00 00 de ad be ef` appended after `fee_recipient`.
6. MPC signs `keccak(V2_bytes)` → signature `σ`.
7. Relayer submits `FinalizeTransferPayload` + `σ` to Solana.
8. Solana calls `serialize_for_near()` → V1 bytes (no message suffix).
9. `keccak(V1_bytes) ≠ keccak(V2_bytes)` → `secp256k1_recover` returns wrong address → `SignatureVerificationFailed`.
10. Transaction reverts. Transfer remains in NEAR `pending_transfers` forever. Funds are permanently locked.

### Citations

**File:** near/omni-types/src/lib.rs (L484-496)
```rust
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct InitTransferMsg {
    pub recipient: OmniAddress,
    pub fee: U128,
    pub native_token_fee: U128,
    /// Optional caller-supplied destination-chain hook payload. Length-capped to
    /// [`MAX_INIT_TRANSFER_MSG_LEN`] bytes to prevent unbounded storage/gas inflation.
    pub msg: Option<BoundedString<MAX_INIT_TRANSFER_MSG_LEN>>,
    /// Optional caller-provided identifier mixed into the virtual storage account ID hash.
    /// Lets otherwise-identical transfers derive distinct storage accounts so their
    /// storage deposits do not collide. Length-capped to [`MAX_EXTERNAL_ID_LEN`] bytes.
    pub external_id: Option<BoundedString<MAX_EXTERNAL_ID_LEN>>,
}
```

**File:** near/omni-types/src/lib.rs (L644-692)
```rust
#[near(serializers=[borsh, json])]
#[derive(Debug, Clone)]
pub struct TransferMessagePayloadV1 {
    pub prefix: PayloadType,
    pub destination_nonce: Nonce,
    pub transfer_id: TransferId,
    pub token_address: OmniAddress,
    pub amount: U128,
    pub recipient: OmniAddress,
    pub fee_recipient: Option<AccountId>,
}

impl From<TransferMessagePayload> for TransferMessagePayloadV1 {
    fn from(payload: TransferMessagePayload) -> Self {
        Self {
            prefix: payload.prefix,
            destination_nonce: payload.destination_nonce,
            transfer_id: payload.transfer_id,
            token_address: payload.token_address,
            amount: payload.amount,
            recipient: payload.recipient,
            fee_recipient: payload.fee_recipient,
        }
    }
}

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

**File:** near/omni-bridge/src/lib.rs (L487-499)
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
```

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L18-44)
```rust
impl Payload for FinalizeTransferPayload {
    type AdditionalParams = (Pubkey, Pubkey); // mint, recipient
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
