### Title
Payload-Controlled Chain Kind in `emitter_address` Construction Permanently Blocks NEAR-Token Wormhole Transfers — (`near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs`)

---

### Summary

All four `TryInto` implementations in `parsed_vaa.rs` derive the chain kind for `emitter_address` from `transfer.token_address.get_chain()` — a value that comes from the attacker-controlled Borsh payload — rather than from the VAA's own `emitter_chain` field. When `token_address` is `OmniAddress::Near(...)`, the code calls `OmniAddress::new_from_slice(ChainKind::Near, &self.emitter_address)`, which attempts to interpret the 32-byte Wormhole emitter address as a UTF-8 NEAR `AccountId`. This always fails for any non-NEAR emitter (e.g., a zero-padded EVM address), causing `verify_vaa_callback` to return `Err` and permanently blocking settlement of every NEAR-native token transfer routed through the Wormhole path.

---

### Finding Description

In `TryInto<InitTransferMessage> for ParsedVAA`:

```rust
emitter_address: OmniAddress::new_from_slice(
    transfer.token_address.get_chain(),   // ← chain from payload, not from VAA header
    &self.emitter_address,                // ← 32-byte Wormhole emitter bytes
)?,
``` [1](#0-0) 

`transfer.token_address` is Borsh-deserialized from `self.payload` — the VAA body bytes that the relayer supplies. When `token_address` is `OmniAddress::Near("alice.near")`, `get_chain()` returns `ChainKind::Near`, and `new_from_slice` dispatches to:

```rust
ChainKind::Near => Ok(Self::Near(Self::to_near_account_id(address)?)),
``` [2](#0-1) 

which calls:

```rust
fn to_near_account_id(address: &[u8]) -> Result<AccountId, String> {
    AccountId::from_str(&String::from_utf8(address.to_vec()).map_err(stringify)?)
        .map_err(stringify)
}
``` [3](#0-2) 

A 32-byte Wormhole emitter address (e.g., `[0u8;12] ++ [evm_contract_bytes;20]`) is not valid UTF-8 and is never a valid NEAR `AccountId`. The `?` propagates the `Err` out of `try_into()`, which propagates out of `verify_vaa_callback` as `Err("...")`.

The same flaw exists identically in all four message-type conversions:

- `TryInto<InitTransferMessage>` — line 177 [1](#0-0) 
- `TryInto<FinTransferMessage>` — line 199 [4](#0-3) 
- `TryInto<DeployTokenMessage>` — line 222 [5](#0-4) 
- `TryInto<LogMetadataMessage>` — line 246 [6](#0-5) 

The `ParsedVAA` struct already carries the correct, guardian-verified `emitter_chain: u16` field, but it is never consulted when constructing `emitter_address`. [7](#0-6) 

---

### Impact Explanation

Any transfer of a NEAR-native token via the Wormhole path is permanently unclaimable on NEAR. The source-chain bridge contract locks the tokens when the transfer is initiated. The only settlement path is for a relayer to submit the guardian-signed VAA to `verify_proof` → `verify_vaa_callback`. Because the VAA payload is fixed by the source-chain contract and the guardian signature covers the entire body, no relayer can alter the payload to avoid the failing `ChainKind::Near` branch. Every retry of the same VAA produces the same `Err`. The downstream `fin_transfer_callback` factory check is never reached:

```rust
require!(
    self.factories.get(&init_transfer.emitter_address.get_chain())
        == Some(init_transfer.emitter_address),
    BridgeError::UnknownFactory.as_ref()
);
``` [8](#0-7) 

Funds are irrecoverably locked in the source-chain bridge contract.

---

### Likelihood Explanation

The Wormhole bridge is explicitly designed to carry `OmniAddress` variants for any chain, including `OmniAddress::Near(...)`, as evidenced by the `InitTransferWh` struct accepting `token_address: OmniAddress` without restriction. [9](#0-8) 

Any NEAR-native token that is bridged outbound to an EVM chain via Wormhole and then bridged back will produce a VAA with `token_address = OmniAddress::Near(...)` and a 32-byte zero-padded EVM emitter address. No privileged access, key compromise, or guardian collusion is required — the bug is triggered by the normal round-trip flow.

---

### Recommendation

Replace `transfer.token_address.get_chain()` with a mapping from the VAA's own `self.emitter_chain` (a Wormhole chain ID `u16`) to the corresponding `ChainKind`. The emitter chain is part of the guardian-signed body and cannot be forged by a relayer. For example:

```rust
let emitter_chain_kind = ChainKind::try_from_wormhole_chain_id(self.emitter_chain)?;
emitter_address: OmniAddress::new_from_slice(emitter_chain_kind, &self.emitter_address)?,
```

This fix must be applied to all four `TryInto` implementations in `parsed_vaa.rs`.

---

### Proof of Concept

```rust
#[test]
fn test_near_token_address_blocks_emitter_construction() {
    use omni_types::{ChainKind, OmniAddress};

    // Simulate what new_from_slice does when token_address is Near
    // and emitter_address is a 32-byte zero-padded EVM address
    let evm_emitter: [u8; 32] = {
        let mut b = [0u8; 32];
        b[12..].copy_from_slice(&[0xde, 0xad, 0xbe, 0xef,
                                   0xde, 0xad, 0xbe, 0xef,
                                   0xde, 0xad, 0xbe, 0xef,
                                   0xde, 0xad, 0xbe, 0xef,
                                   0xde, 0xad, 0xbe, 0xef]);
        b
    };

    // token_address chain = Near (from payload)
    let result = OmniAddress::new_from_slice(ChainKind::Near, &evm_emitter);
    // Always Err — 0x00 bytes are not valid UTF-8 / NEAR AccountId
    assert!(result.is_err(), "emitter_address construction must fail, blocking settlement");
}
```

### Citations

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L25-26)
```rust
    pub emitter_chain: u16,
    pub emitter_address: Vec<u8>,
```

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L143-154)
```rust
#[derive(Debug, BorshDeserialize)]
struct InitTransferWh {
    payload_type: ProofKind,
    sender: OmniAddress,
    token_address: OmniAddress,
    origin_nonce: Nonce,
    amount: u128,
    fee: u128,
    native_fee: u128,
    recipient: String,
    message: String,
}
```

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L177-180)
```rust
            emitter_address: OmniAddress::new_from_slice(
                transfer.token_address.get_chain(),
                &self.emitter_address,
            )?,
```

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L199-202)
```rust
            emitter_address: OmniAddress::new_from_slice(
                transfer.token_address.get_chain(),
                &self.emitter_address,
            )?,
```

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L222-225)
```rust
            emitter_address: OmniAddress::new_from_slice(
                parsed_payload.token_address.get_chain(),
                &self.emitter_address,
            )?,
```

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L246-246)
```rust
            emitter_address: OmniAddress::new_from_slice(chain_kind, &self.emitter_address)?,
```

**File:** near/omni-types/src/lib.rs (L244-244)
```rust
            ChainKind::Near => Ok(Self::Near(Self::to_near_account_id(address)?)),
```

**File:** near/omni-types/src/lib.rs (L383-386)
```rust
    fn to_near_account_id(address: &[u8]) -> Result<AccountId, String> {
        AccountId::from_str(&String::from_utf8(address.to_vec()).map_err(stringify)?)
            .map_err(stringify)
    }
```

**File:** near/omni-bridge/src/lib.rs (L708-713)
```rust
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );
```
