I can see the core issue in `parsed_vaa.rs`. Now I need to check the bridge's factory validation logic to confirm the downstream impact.

I have all the code I need. Let me trace the full exploit path precisely.

**Key finding in `parsed_vaa.rs` lines 177–180:**

```rust
emitter_address: OmniAddress::new_from_slice(
    transfer.token_address.get_chain(),  // ← from PAYLOAD (attacker-influenced)
    &self.emitter_address,               // ← 32-byte emitter from VAA header
)?,
```

The chain kind used to interpret the emitter address comes from `transfer.token_address.get_chain()` — the Borsh payload field — **not** from `self.emitter_chain` (the VAA's actual emitter chain). The `emitter_chain` field is parsed at line 92 but **never used** in any of the four `TryInto` implementations.

Now checking `fin_transfer_callback` lines 708–713:

```rust
require!(
    self.factories.get(&init_transfer.emitter_address.get_chain())
        == Some(init_transfer.emitter_address),
    BridgeError::UnknownFactory.as_ref()
);
```

And `to_sol_address` (lib.rs lines 376–380):

```rust
fn to_sol_address(address: &[u8]) -> Result<SolAddress, String> {
    address.try_into().map_or_else(
        |_| Err("Invalid SOL address".to_string()),
        |bytes| Ok(SolAddress(bytes)),
    )
}
```

Any 32-byte slice is accepted as a valid `SolAddress`. The VAA emitter address is always 32 bytes.

---

### Title
Emitter address chain kind derived from payload `token_address` field instead of VAA `emitter_chain`, causing permanent `UnknownFactory` rejection for cross-chain token transfers — (`near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs`)

### Summary

All four `TryInto` implementations in `parsed_vaa.rs` compute `emitter_address` by calling `OmniAddress::new_from_slice(token_address.get_chain(), &self.emitter_address)`. The chain kind argument is taken from the Borsh payload's `token_address` field rather than from the VAA's `emitter_chain` field. The `emitter_chain` field is parsed but never consumed. When `token_address` belongs to a different chain than the actual VAA emitter (e.g., a Solana-native token being transferred from an EVM bridge), the resulting `emitter_address` has the wrong chain kind and wrong address bytes, causing the factory check in `fin_transfer_callback` to permanently reject the transfer.

### Finding Description

In `parsed_vaa.rs`, `TryInto<InitTransferMessage>`:

```rust
emitter_address: OmniAddress::new_from_slice(
    transfer.token_address.get_chain(),   // BUG: should be ChainKind from self.emitter_chain
    &self.emitter_address,
)?,
``` [1](#0-0) 

The same pattern appears in `TryInto<FinTransferMessage>`, `TryInto<DeployTokenMessage>`, and `TryInto<LogMetadataMessage>`. [2](#0-1) [3](#0-2) 

The `emitter_chain: u16` field is parsed from the VAA body at line 92 and stored in `ParsedVAA`, but is never referenced in any conversion: [4](#0-3) 

`OmniAddress::new_from_slice(ChainKind::Sol, &32_byte_slice)` always succeeds because `to_sol_address` performs only a length check (`[u8; 32]`): [5](#0-4) 

In `fin_transfer_callback`, the bridge checks the computed `emitter_address` against the registered factory map:

```rust
require!(
    self.factories.get(&init_transfer.emitter_address.get_chain())
        == Some(init_transfer.emitter_address),
    BridgeError::UnknownFactory.as_ref()
);
``` [6](#0-5) 

The `factories` map is keyed by `ChainKind` and stores the registered factory address per chain: [7](#0-6) 

### Impact Explanation

Consider a legitimate transfer of a Solana-native token from an EVM bridge (e.g., Ethereum) to NEAR:

- VAA `emitter_chain = 2` (Ethereum/Wormhole chain ID), `emitter_address = [0x00…00 <20-byte EVM contract>]` (32 bytes, EVM-padded)
- Borsh payload `token_address = OmniAddress::Sol(real_sol_token_pubkey)`

The proxy computes:
```
emitter_address = new_from_slice(ChainKind::Sol, &evm_emitter_32bytes)
               = OmniAddress::Sol(SolAddress(evm_emitter_32bytes))
```

`fin_transfer_callback` then looks up `factories.get(ChainKind::Sol)`, which returns the registered Solana bridge program address — not `OmniAddress::Sol(evm_emitter_32bytes)`. The `require!` panics with `UnknownFactory`. The transaction reverts without consuming the VAA or marking the transfer as finalized. Every subsequent retry produces the same result. The user's funds locked in the EVM bridge contract cannot be released on NEAR, constituting a permanent, irrecoverable settlement block.

### Likelihood Explanation

The Omni Bridge explicitly supports multi-chain token flows. Solana-native tokens can be wrapped and held on EVM chains; when a user initiates a return transfer from EVM, the EVM bridge emits an `InitTransfer` VAA with `token_address` encoding the original Solana token address. This is a standard, documented bridge use case. No guardian compromise or privileged access is required — any relayer submitting such a legitimate guardian-signed VAA triggers the bug. The `emitter_chain` field being parsed but silently ignored in all four conversion paths confirms this is a systematic coding error, not an edge case.

### Recommendation

Replace `token_address.get_chain()` with a mapping from `self.emitter_chain` (the Wormhole `u16` chain ID) to `ChainKind` in all four `TryInto` implementations. The `emitter_chain` field is already parsed and available on `ParsedVAA`; it should be the sole source of truth for the chain kind used to interpret `emitter_address`.

```rust
// Correct fix:
let emitter_chain_kind = ChainKind::from_wormhole_chain_id(self.emitter_chain)?;
emitter_address: OmniAddress::new_from_slice(emitter_chain_kind, &self.emitter_address)?,
```

### Proof of Concept

1. Deploy the Omni Bridge on NEAR testnet with a registered EVM factory `OmniAddress::Eth(0xABCD…)` and a registered Solana factory `OmniAddress::Sol(real_sol_program)`.
2. Construct a guardian-signed Wormhole VAA with:
   - `emitter_chain = 2` (Ethereum)
   - `emitter_address = [0x00…00 AB CD … ]` (EVM address, 32-byte padded)
   - Borsh payload: `InitTransferWh { token_address: OmniAddress::Sol(some_32_bytes), … }`
3. Submit via `fin_transfer(chain_kind=Eth, prover_args=<wormhole VAA>)`.
4. Observe `verify_vaa_callback` produces `InitTransferMessage { emitter_address: OmniAddress::Sol(evm_emitter_bytes), … }`.
5. Observe `fin_transfer_callback` panics: `factories.get(ChainKind::Sol) = Some(real_sol_program) ≠ Some(OmniAddress::Sol(evm_emitter_bytes))` → `UnknownFactory`.
6. Confirm the transfer cannot be finalized on any retry; user funds on EVM are permanently locked.

### Citations

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L92-95)
```rust
        let emitter_chain = data.get_u16(body_offset + Self::VAA_EMITTER_CHAIN_POS);
        let emitter_address = data
            .get_bytes32(body_offset + Self::VAA_EMITTER_ADDRESS_POS)
            .to_vec();
```

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L177-180)
```rust
            emitter_address: OmniAddress::new_from_slice(
                transfer.token_address.get_chain(),
                &self.emitter_address,
            )?,
```

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L199-203)
```rust
            emitter_address: OmniAddress::new_from_slice(
                transfer.token_address.get_chain(),
                &self.emitter_address,
            )?,
        })
```

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L222-226)
```rust
            emitter_address: OmniAddress::new_from_slice(
                parsed_payload.token_address.get_chain(),
                &self.emitter_address,
            )?,
        })
```

**File:** near/omni-types/src/lib.rs (L376-380)
```rust
    fn to_sol_address(address: &[u8]) -> Result<SolAddress, String> {
        address.try_into().map_or_else(
            |_| Err("Invalid SOL address".to_string()),
            |bytes| Ok(SolAddress(bytes)),
        )
```

**File:** near/omni-bridge/src/lib.rs (L221-221)
```rust
    pub factories: LookupMap<ChainKind, OmniAddress>,
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
