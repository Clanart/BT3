The code is clear. Let me confirm the exact lines for the finding.

**`MetadataPayload::to_borsh`** — `starknet/src/bridge_types.cairo` lines 36–44:
- Encodes only: `PayloadType::Metadata` byte, `token`, `name`, `symbol`, `decimals`. **No `chain_id`, no contract address.**

**`TransferMessagePayload::to_borsh(chain_id)`** — same file, lines 61–84:
- Explicitly encodes `chain_id` twice (lines 67, 70). Domain-separated.

**`deploy_token`** — `starknet/src/omni_bridge.cairo` line 205:
- Calls `_verify_borsh_signature(ref self, @payload.to_borsh(), signature)` — no chain_id passed.

**`fin_transfer`** — same file, line 253:
- Calls `_verify_borsh_signature(ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature)` — chain_id IS included.

**`_verify_borsh_signature`** — lines 398–406:
- Computes `keccak(borsh_bytes)` and verifies against `self.omni_bridge_derived_address`. No chain/contract binding for deploy_token path.

The `omni_bridge_chain_id` is stored in contract state (line 117) and used in `fin_transfer` but silently omitted from `deploy_token`. This is an asymmetry, not a design choice.

---

### Title
Missing Chain-ID Domain Separation in `MetadataPayload::to_borsh` Enables Cross-Instance `deploy_token` Signature Replay — (`starknet/src/bridge_types.cairo`)

### Summary

`MetadataPayload::to_borsh` produces identical bytes regardless of which StarkNet bridge instance (chain) it is called on. A valid NEAR-MPC-signed `deploy_token` signature observed on one bridge instance can be submitted verbatim to any other bridge instance sharing the same `omni_bridge_derived_address`, because `_verify_borsh_signature` has no chain or contract binding for this payload type.

### Finding Description

`MetadataPayload::to_borsh` serializes only `[PayloadType::Metadata, token, name, symbol, decimals]`: [1](#0-0) 

By contrast, `TransferMessagePayload::to_borsh` explicitly encodes `chain_id` (twice) to bind the signature to a specific destination: [2](#0-1) 

`deploy_token` calls `_verify_borsh_signature` with the chain-id-free borsh output: [3](#0-2) 

While `fin_transfer` correctly passes `self.omni_bridge_chain_id.read()`: [4](#0-3) 

`_verify_borsh_signature` verifies only `keccak(borsh_bytes)` against `omni_bridge_derived_address` — no chain or contract address is mixed in: [5](#0-4) 

The `omni_bridge_chain_id` is stored in contract state and available at the call site, but is never passed to `MetadataPayload::to_borsh`: [6](#0-5) 

### Impact Explanation

An attacker who observes a valid `(signature, MetadataPayload)` pair accepted by bridge instance A can submit the identical pair to bridge instance B, provided both share the same `omni_bridge_derived_address`. The signature check passes because the signed bytes are identical. The `ERR_TOKEN_ALREADY_DEPLOYED` guard only prevents replay within the same instance; it does not protect cross-instance. The result is unauthorized token deployment on the second bridge, hijacking the canonical `near_to_starknet_token` mapping for that NEAR token ID on that instance before the legitimate deployment occurs.

### Likelihood Explanation

The precondition — two bridge instances sharing the same `omni_bridge_derived_address` — is realistic: the derived address is the Ethereum-style address of the NEAR MPC key. If the same NEAR bridge contract (and thus the same MPC key) authorizes deployments on multiple StarkNet instances (e.g., a staging deployment alongside mainnet, or two bridge versions during a migration), the precondition is met. The asymmetry with `fin_transfer` (which does include `chain_id`) strongly suggests this omission is unintentional rather than a deliberate design choice.

### Recommendation

Pass `omni_bridge_chain_id` into `MetadataPayload::to_borsh` (mirroring the `TransferMessagePayload` pattern) and append it to the serialized bytes. Update `deploy_token` to call `payload.to_borsh(self.omni_bridge_chain_id.read())`.

### Proof of Concept

```cairo
// Both instances share omni_bridge_derived_address = ADDR, chain_ids 1 and 2.
let payload = MetadataPayload { token: "tok.near", name: "Tok", symbol: "TOK", decimals: 18 };

// Attacker observes valid signature on instance A (chain_id=1):
let borsh_A = payload.to_borsh(); // [1, "tok.near", "Tok", "TOK", 18]

// Compute borsh on instance B (chain_id=2):
let borsh_B = payload.to_borsh(); // [1, "tok.near", "Tok", "TOK", 18]  ← identical

assert(borsh_A == borsh_B); // passes — no chain_id in either

// Attacker submits (sig_A, payload) to instance B → _verify_borsh_signature passes
// → token deployed on instance B without authorization
```

### Citations

**File:** starknet/src/bridge_types.cairo (L36-44)
```text
    fn to_borsh(self: @MetadataPayload) -> ByteArray {
        let mut borsh_bytes: ByteArray = Default::default();
        borsh_bytes.append_byte(PayloadType::Metadata.into());
        borsh_bytes.append(@borsh::encode_byte_array(self.token));
        borsh_bytes.append(@borsh::encode_byte_array(self.name));
        borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
        borsh_bytes.append_byte(*self.decimals);
        borsh_bytes
    }
```

**File:** starknet/src/bridge_types.cairo (L61-70)
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
```

**File:** starknet/src/omni_bridge.cairo (L117-118)
```text
        omni_bridge_chain_id: u8,
        omni_bridge_derived_address: EthAddress,
```

**File:** starknet/src/omni_bridge.cairo (L202-205)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);
```

**File:** starknet/src/omni_bridge.cairo (L252-254)
```text
            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );
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
