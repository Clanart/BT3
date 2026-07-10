### Title
Inconsistent Borsh `Option` Encoding of `message` Field in `TransferMessagePayload::to_borsh()` Causes Signature Verification Failure — (`starknet/src/bridge_types.cairo`)

---

### Summary

In `TransferMessagePayloadImpl::to_borsh()`, the `message: Option<ByteArray>` field is serialized without the standard Borsh `Option` discriminant byte, unlike the `fee_recipient: Option<ByteArray>` field in the same function. This produces a different byte sequence than what the NEAR-side MPC signer produces, causing `fin_transfer` to always fail signature verification for any transfer that carries a `message` payload, permanently freezing bridged funds on the source chain.

---

### Finding Description

In `starknet/src/bridge_types.cairo`, `TransferMessagePayloadImpl::to_borsh()` serializes the two `Option<ByteArray>` fields differently:

**`fee_recipient` — correct standard Borsh `Option` encoding:** [1](#0-0) 

```cairo
match self.fee_recipient {
    Option::None => { borsh_bytes.append_byte(0); },   // 0x00 discriminant
    Option::Some(fee_recipient) => {
        borsh_bytes.append_byte(1);                    // 0x01 discriminant
        borsh_bytes.append(@borsh::encode_byte_array(fee_recipient));
    },
}
```

**`message` — missing discriminant bytes entirely:** [2](#0-1) 

```cairo
match self.message {
    Option::None => {},                                // ← no 0x00 byte appended
    Option::Some(message) => {
        borsh_bytes.append(@borsh::encode_byte_array(message)); // ← no 0x01 prefix
    },
}
```

Standard Borsh (used by the NEAR Rust `borsh` crate) encodes `Option<T>` as:
- `None` → `0x00`
- `Some(v)` → `0x01 || borsh(v)`

The `fee_recipient` field follows this correctly. The `message` field does not. The NEAR-side MPC signer serializes the full `TransferMessage` struct using the Rust `borsh` crate, which will include the `0x00`/`0x01` discriminant for `message`. The StarkNet side omits it entirely.

The resulting byte arrays diverge, so `compute_keccak_byte_array` produces a different hash, and `verify_eth_signature` rejects the MPC-produced signature: [3](#0-2) 

---

### Impact Explanation

Any `fin_transfer` call that includes a non-`None` `message` field will always revert at signature verification. The user's tokens were already burned or locked on the source chain during `init_transfer`. Because the destination-side finalization can never succeed, those funds are permanently unclaimable — matching the **Critical: permanent freezing / irrecoverable lock of user funds** impact class.

Even for `message = None`, the StarkNet side appends zero bytes while the NEAR side appends `0x00`, producing a 1-byte hash divergence that also causes all such transfers to fail if the NEAR side encodes `message` with the standard discriminant.

---

### Likelihood Explanation

- Any bridge user who calls `init_transfer` with a non-empty `message` (a publicly accessible parameter) triggers this path.
- The `message` field is part of the public `init_transfer` interface: [4](#0-3) 
- No privileged role is required. The attacker is simply a normal bridge user whose funds become permanently frozen.
- The bug is latent until a transfer with `message != None` is attempted; it will not appear in tests that only exercise `message = None` transfers.

---

### Recommendation

Apply the same standard Borsh `Option` encoding to `message` as is already correctly applied to `fee_recipient`:

```cairo
match self.message {
    Option::None => { borsh_bytes.append_byte(0); },
    Option::Some(message) => {
        borsh_bytes.append_byte(1);
        borsh_bytes.append(@borsh::encode_byte_array(message));
    },
}
```

This aligns the StarkNet serialization with the NEAR Rust `borsh` crate's `Option<T>` encoding and ensures the keccak hash matches what the MPC signs.

---

### Proof of Concept

1. User calls `init_transfer` on StarkNet with `message = "hello"`. Tokens are burned/locked.
2. NEAR MPC observes the event and signs a `TransferMessage` payload. The NEAR Rust `borsh` crate serializes `message` as `0x01 || encode_u32(5) || "hello"`.
3. Relayer calls `fin_transfer` on StarkNet with the MPC signature and the same payload.
4. StarkNet's `to_borsh()` serializes `message` as `encode_u32(5) || "hello"` (no `0x01` prefix).
5. `compute_keccak_byte_array` produces a different hash than what the MPC signed.
6. `verify_eth_signature` panics / reverts — the transfer can never be finalized.
7. User's funds are permanently locked with no recovery path.

The divergence is exactly 1 byte (`0x01`) for `Some` and 1 byte (`0x00`) for `None`, making hash collision impossible and the failure deterministic for every affected transfer. [5](#0-4)

### Citations

**File:** starknet/src/bridge_types.cairo (L61-84)
```text
    fn to_borsh(self: @TransferMessagePayload, chain_id: u8) -> ByteArray {
        let mut borsh_bytes: ByteArray = Default::default();
        borsh_bytes.append_byte(PayloadType::TransferMessage.into());
        borsh_bytes.append(@borsh::encode_u64(*self.destination_nonce));
        borsh_bytes.append_byte(*self.origin_chain);
        borsh_bytes.append(@borsh::encode_u64(*self.origin_nonce));
        borsh_bytes.append_byte(chain_id);
        borsh_bytes.append(@borsh::encode_address(*self.token_address));
        borsh_bytes.append(@borsh::encode_u128(*self.amount));
        borsh_bytes.append_byte(chain_id);
        borsh_bytes.append(@borsh::encode_address(*self.recipient));
        match self.fee_recipient {
            Option::None => { borsh_bytes.append_byte(0); },
            Option::Some(fee_recipient) => {
                borsh_bytes.append_byte(1);
                borsh_bytes.append(@borsh::encode_byte_array(fee_recipient));
            },
        }
        match self.message {
            Option::None => {},
            Option::Some(message) => { borsh_bytes.append(@borsh::encode_byte_array(message)); },
        }
        borsh_bytes
    }
```

**File:** starknet/src/omni_bridge.cairo (L281-291)
```text
        fn init_transfer(
            ref self: ContractState,
            token_address: ContractAddress,
            amount: u128,
            fee: u128,
            native_fee: u128,
            recipient: ByteArray,
            message: ByteArray,
        ) {
            assert(!_is_paused(@self, PAUSE_INIT_TRANSFER), 'ERR_INIT_TRANSFER_PAUSED');

```

**File:** starknet/src/omni_bridge.cairo (L398-406)
```text
    fn _verify_borsh_signature(
        ref self: ContractState, borsh_bytes: @ByteArray, signature: Signature,
    ) {
        let message_hash_le = compute_keccak_byte_array(borsh_bytes);
        let message_hash = reverse_u256_bytes(message_hash_le);

        let sig = signature_from_vrs(signature.v, signature.r, signature.s);
        verify_eth_signature(message_hash, sig, self.omni_bridge_derived_address.read());
    }
```
