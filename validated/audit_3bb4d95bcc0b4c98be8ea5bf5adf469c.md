### Title
Cross-Chain Replay of `deployToken` Signature Enables Unauthorized Token Deployment on Unintended Chains — (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The Borsh-encoded payload signed by the NEAR MPC bridge for `deployToken` / `deploy_token` operations is **identical across EVM, Starknet, and Solana** because no chain identifier is included in the signed data. A single valid signature observed on one chain can be replayed verbatim on every other chain, deploying the bridged token there without the NEAR bridge's authorization for that chain.

---

### Finding Description

Every chain's `deployToken` path constructs the signed message as:

```
0x01 | borsh(token_id) | borsh(name) | borsh(symbol) | decimals
```

**EVM** (`OmniBridge.sol`, lines 142–149):
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),   // 0x01
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
bytes32 hashed = keccak256(borshEncoded);
``` [1](#0-0) 

**Starknet** (`bridge_types.cairo`, lines 36–44):
```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());  // 0x01
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);
``` [2](#0-1) 

**Solana** (`deploy_token.rs`, lines 19–26):
```rust
fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
    IncomingMessageType::Metadata.serialize(&mut writer)?;  // variant 1 = 0x01
    self.serialize(&mut writer)?;  // {token, name, symbol, decimals}
``` [3](#0-2) 

All three chains use `0x01` as the type prefix (`PayloadType::Metadata` / `IncomingMessageType::Metadata` are all enum variant index 1), and all use the same borsh string encoding (4-byte LE length + UTF-8 bytes). The resulting byte sequences are byte-for-byte identical for the same token metadata.

**Contrast with `finTransfer`**, which correctly binds the signature to a specific chain by including `omniBridgeChainId` twice in the signed data: [4](#0-3) [5](#0-4) [6](#0-5) 

`deployToken` has no equivalent chain-binding field.

---

### Impact Explanation

An attacker who observes a valid `deployToken` transaction on Chain A (e.g., Ethereum) can extract the signature and replay it on Chain B (e.g., Starknet or Solana) with the identical payload. The result:

1. **Unauthorized token deployment on Chain B**: The token is registered in Chain B's bridge mappings (`nearToEthToken` / `near_to_starknet_token` / wrapped mint PDA) without the NEAR bridge having authorized deployment on Chain B.
2. **Permanent blocking of legitimate deployment**: Once the token exists on Chain B, the bridge's own deployment attempt will revert with `ERR_TOKEN_EXIST` / `ERR_TOKEN_ALREADY_DEPLOYED` / equivalent, permanently preventing the NEAR bridge from deploying the token through its normal authorized flow on that chain.
3. **Unbacked supply risk**: If the NEAR bridge subsequently signs `finTransfer` messages for Chain B (using the correct chain ID), those will succeed against the attacker-pre-deployed token, minting tokens on Chain B that were never properly authorized for that chain's deployment lifecycle.

This matches the allowed High impact: **"signature verification bypass enabling unauthorized token deployment."**

---

### Likelihood Explanation

- All `deployToken` transactions are public on-chain; any observer can extract the signature and payload.
- The attacker needs no privileged access — only the ability to call `deployToken` on another chain's OmniBridge contract.
- The attack is viable any time a new token is deployed on any one chain before it is deployed on the others, which is the normal sequential deployment pattern.

---

### Recommendation

Include the destination chain identifier in the signed metadata payload, mirroring the pattern already used in `finTransfer`. For example, in EVM:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // ← add chain binding
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Apply the equivalent change to Starknet's `MetadataPayload.to_borsh()` and Solana's `DeployTokenPayload::serialize_for_near()`, and update the NEAR MPC signing logic to include the target chain ID when producing `deployToken` signatures.

---

### Proof of Concept

1. NEAR bridge deploys token `wrap.near` on Ethereum (Chain A, `omniBridgeChainId = 0`). The signed bytes are:
   ```
   01 | 09 00 00 00 "wrap.near" | 0b 00 00 00 "Wrapped NEAR" | 05 00 00 00 "wNEAR" | 18
   ```
   Signature `σ` is emitted in the `deployToken` calldata on Ethereum.

2. Attacker observes `σ` and the payload on Ethereum.

3. Attacker calls `deploy_token(σ, payload)` on the Starknet OmniBridge (Chain B, `omni_bridge_chain_id = 3`).

4. Starknet's `_verify_borsh_signature` hashes the identical byte sequence and recovers the same `omni_bridge_derived_address` — the signature is accepted. [7](#0-6) 

5. `wrap.near` is now registered in Starknet's `near_to_starknet_token` mapping. When the NEAR bridge later attempts to deploy `wrap.near` on Starknet through its normal flow, it reverts with `ERR_TOKEN_ALREADY_DEPLOYED`. [8](#0-7)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L142-149)
```text
        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
            Borsh.encodeString(metadata.token),
            Borsh.encodeString(metadata.name),
            Borsh.encodeString(metadata.symbol),
            bytes1(metadata.decimals)
        );
        bytes32 hashed = keccak256(borshEncoded);
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L289-308)
```text
        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
```

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

**File:** solana/programs/bridge_token_factory/src/state/message/deploy_token.rs (L19-26)
```rust
    fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
        let mut writer = BufWriter::new(Vec::with_capacity(DEFAULT_SERIALIZER_CAPACITY));
        IncomingMessageType::Metadata.serialize(&mut writer)?;
        self.serialize(&mut writer)?; // borsh encoding
        writer
            .into_inner()
            .map_err(|_| error!(ErrorCode::InvalidArgs))
    }
```

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L30-36)
```rust
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.0.serialize(&mut writer)?;
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. recipient
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.1.serialize(&mut writer)?;
```

**File:** starknet/src/omni_bridge.cairo (L207-209)
```text
            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');
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
