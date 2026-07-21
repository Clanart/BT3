### Title
`channel` field excluded from `PropellerUnit` publisher signature, enabling cross-channel shard injection - (File: `crates/apollo_propeller/src/signature.rs`)

### Summary
The Propeller protocol's publisher signature covers only the `message_root` (merkle root of shards). The `channel` field — which identifies which logical consensus channel a unit belongs to — is never included in the signed payload. A malicious peer that receives a valid `PropellerUnit` for channel A can mutate the `channel` field to B and forward it; the signature still verifies, and the engine accepts and processes the unit under channel B's `MessageProcessor`. This is the direct sequencer analog of H-29: a type/context discriminator is absent from the signed commitment, allowing the same signed data to be accepted under a different context.

### Finding Description

**Signing (sender side)** — `crates/apollo_propeller/src/sharding.rs` line 106:

```rust
let signature = signature::sign_message_id(&message_root, &keypair)?;
```

`sign_message_id` constructs the signed payload as:

```rust
// crates/apollo_propeller/src/signature.rs lines 17-24
pub fn sign_message_id(message_id: &MessageRoot, keypair: &Keypair) -> ... {
    let msg = [SIGNING_PREFIX, &message_id.0, SIGNING_POSTFIX].concat();
    keypair.sign(&msg)
}
```

Only `message_root` bytes are signed. `channel` is never included.

**Verification (receiver side)** — `crates/apollo_propeller/src/unit_validator.rs` lines 71-75:

```rust
let result = signature::verify_message_id_signature(
    &unit.root(),
    unit.signature(),
    &self.publisher_public_key,
);
```

`verify_message_id_signature` reconstructs the same `[SIGNING_PREFIX, &message_id.0, SIGNING_POSTFIX]` payload and verifies against it. The `channel` field from the unit is never part of the verification.

**Engine routing** — `crates/apollo_propeller/src/engine.rs` lines 215-234:

```rust
let claimed_channel = unit.channel();   // taken directly from the wire unit
// ...
let message_key = MessageKey {
    channel: claimed_channel,           // attacker-controlled
    publisher: claimed_publisher,
    root: claimed_root,
};
```

The engine creates a new `MessageProcessor` keyed on `(claimed_channel, publisher, root)`. The `UnitValidator` is then initialised with `claimed_channel` and its internal assertion `assert_eq!(self.channel, unit.channel())` is circular — it only checks that the unit's channel matches the channel the validator was itself created with, providing no independent binding.

**Proto definition** — `crates/apollo_protobuf/src/proto/p2p/proto/propeller/propeller.proto` lines 29-34:

```proto
bytes signature = 6;  // over merkle_root only
uint32 channel = 7;   // not covered by signature
```

The proto comment explicitly states the signature is "over the merkle_root", confirming `channel` is outside the signed scope.

### Impact Explanation

A malicious peer that receives a legitimately signed `PropellerUnit` for channel A (e.g., consensus committee round N) can:

1. Copy the unit verbatim.
2. Overwrite the `channel` field to B (e.g., round N+1 or a different committee).
3. Broadcast the modified unit to other peers.

Because `verify_message_id_signature` only checks the merkle root, the signature passes. The engine spawns a `MessageProcessor` for `(channel B, publisher, root)`, reconstructs the message, and emits a finalised event attributed to channel B. The channel B consumer receives data that was actually produced for channel A — wrong block proposal data delivered to the wrong consensus round — without any cryptographic evidence of tampering.

This matches the allowed impact: **High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload.** The signature/hash logic fails to bind the channel (type discriminator), so the wrong executable payload is delivered to the wrong channel context.

### Likelihood Explanation

Any peer that participates in the Propeller network and receives a valid unit can perform this attack with zero cryptographic work. No key material needs to be compromised. The attacker only needs to be a connected peer that receives at least one shard from the legitimate publisher. The proto field is a plain `uint32` with no integrity protection beyond the absent signature binding.

### Recommendation

Include `channel` in the signed payload. Change `sign_message_id` and `verify_message_id_signature` to accept and incorporate the channel:

```rust
pub fn sign_message_id(
    message_id: &MessageRoot,
    channel: Channel,
    keypair: &Keypair,
) -> Result<Vec<u8>, ShardPublishError> {
    let channel_bytes = channel.0.to_le_bytes();
    let msg = [SIGNING_PREFIX, &message_id.0, &channel_bytes, SIGNING_POSTFIX].concat();
    keypair.sign(&msg).map_err(|e| ShardPublishError::SigningFailed(e.to_string()))
}
```

Apply the same change to `verify_message_id_signature`. Update `create_units_to_publish` in `sharding.rs` to pass `channel` to `sign_message_id`, and update `UnitValidator::verify_signature` to pass `self.channel` to `verify_message_id_signature`.

### Proof of Concept

```
1. Legitimate publisher P signs and broadcasts units for channel=0, message_root=R.
2. Attacker A receives one such PropellerUnit U (channel=0, root=R, sig=S).
3. A constructs U' = PropellerUnit { channel=1, root=R, sig=S, ... } (all other fields identical).
4. A sends U' to peers registered on channel=1.
5. Each peer calls verify_message_id_signature(root=R, sig=S, pubkey=P_pub):
   - msg = [SIGNING_PREFIX, R.bytes, SIGNING_POSTFIX]  ← channel absent
   - P_pub.verify(msg, S) → Ok(())                     ← passes
6. Engine creates MessageProcessor for (channel=1, publisher=P, root=R).
7. Once enough shards arrive, the message is reconstructed and emitted as a
   channel=1 event, delivering channel=0 block data to the channel=1 consumer.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_propeller/src/signature.rs (L17-24)
```rust
pub fn sign_message_id(
    message_id: &MessageRoot,
    keypair: &Keypair,
) -> Result<Vec<u8>, ShardPublishError> {
    let msg = [SIGNING_PREFIX, &message_id.0, SIGNING_POSTFIX].concat();
    // TODO(AndrewL): Use a transparent error type for this.
    keypair.sign(&msg).map_err(|e| ShardPublishError::SigningFailed(e.to_string()))
}
```

**File:** crates/apollo_propeller/src/sharding.rs (L106-106)
```rust
    let signature = signature::sign_message_id(&message_root, &keypair)?;
```

**File:** crates/apollo_propeller/src/unit_validator.rs (L59-82)
```rust
    fn verify_signature(
        &mut self,
        unit: &PropellerUnit,
    ) -> Result<(), ShardSignatureVerificationError> {
        if let Some(signature) = &self.verified_signature {
            return if signature == unit.signature() {
                Ok(())
            } else {
                Err(ShardSignatureVerificationError::VerificationFailed)
            };
        }

        let result = signature::verify_message_id_signature(
            &unit.root(),
            unit.signature(),
            &self.publisher_public_key,
        );

        if let Ok(()) = &result {
            self.verified_signature = Some(unit.signature().to_vec());
        }

        result
    }
```

**File:** crates/apollo_propeller/src/engine.rs (L214-235)
```rust
    fn handle_unit(&mut self, sender_peer_id: PeerId, unit: PropellerUnit) {
        let claimed_channel = unit.channel();
        let claimed_publisher = unit.publisher();
        let claimed_root = unit.root();

        // Track received shard.
        if let Some(metrics) = &self.metrics {
            metrics.shards_received.increment(1);
        }

        // Check if channel is registered.
        let Some(channel_data) = self.channels.get(&claimed_channel) else {
            warn!(?claimed_channel, "Received shard for unregistered channel, dropping");
            return;
        };

        // Skip if message already finalized.
        let message_key = MessageKey {
            channel: claimed_channel,
            publisher: claimed_publisher,
            root: claimed_root,
        };
```

**File:** crates/apollo_protobuf/src/proto/p2p/proto/propeller/propeller.proto (L29-35)
```text
    // Cryptographic signature from the publisher over the merkle_root.
    bytes signature = 6;
    // TODO(AndrewL): consider re-naming channel
    // TODO(AndrewL): make it uint64 instead of uint32.
    // Logical channel identifier for multiplexing different message streams.
    uint32 channel = 7;
    // TODO(AndrewL): CRITICAL: protect against replay attacks (maybe using a timestamp)
```
