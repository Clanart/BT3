### Title
Cross-Chain Replay of `deployToken`/`deploy_token` Signatures Enables Unauthorized Token Deployment — (Files: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The Borsh-encoded message hashed and signed by the NEAR MPC for `deployToken` / `deploy_token` does **not** include a destination chain identifier. A valid signature obtained from one chain's deployment transaction can be replayed verbatim on any other chain running the OmniBridge, enabling unauthorized token deployment and permanently blocking legitimate deployment on the targeted chain.

---

### Finding Description

**Vulnerability class**: Cross-chain replay / domain-separation flaw in MPC signature verification.

The `finTransfer` / `fin_transfer` path correctly binds the signed message to the destination chain by embedding `omniBridgeChainId` twice in the Borsh payload (once for the token address, once for the recipient address):

**EVM `finTransfer`** — `OmniBridge.sol` lines 289–308:
```solidity
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.tokenAddress),
...
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.recipient),
``` [1](#0-0) 

The `deployToken` path on every chain omits this binding entirely.

**EVM `deployToken`** — `OmniBridge.sol` lines 142–153:
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
bytes32 hashed = keccak256(borshEncoded);
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
```
No chain ID anywhere in the hash. [2](#0-1) 

**Starknet `MetadataPayload::to_borsh()`** — `bridge_types.cairo` lines 36–44:
```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);
    borsh_bytes
}
```
No chain ID. Called without a chain ID argument in `deploy_token` at line 205. [3](#0-2) [4](#0-3) 

Compare directly to `TransferMessagePayload::to_borsh(chain_id: u8)` which accepts and embeds `chain_id` at lines 67 and 70: [5](#0-4) 

**Solana `DeployTokenPayload::serialize_for_near`** — `deploy_token.rs` lines 19–27:
```rust
fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
    IncomingMessageType::Metadata.serialize(&mut writer)?;
    self.serialize(&mut writer)?; // token, name, symbol, decimals only
    ...
}
```
No `SOLANA_OMNI_BRIDGE_CHAIN_ID` written. Compare to `FinalizeTransferPayload::serialize_for_near` which writes `SOLANA_OMNI_BRIDGE_CHAIN_ID` at lines 30 and 35. [6](#0-5) [7](#0-6) 

The result: the Borsh-encoded bytes and their keccak hash are **identical** across all chains for the same token metadata. A single NEAR MPC signature is simultaneously valid on Ethereum, BSC, Polygon, Arbitrum, Starknet, Solana, and any future OmniBridge deployment.

---

### Impact Explanation

An attacker who observes a legitimate `deployToken` transaction on chain A (the signature is public on-chain) can immediately submit the same `(signatureData, metadata)` tuple to the OmniBridge on chain B.

Consequences on chain B:
1. **Unauthorized token deployment**: A `BridgeToken` proxy is deployed and registered in `nearToEthToken` / `near_to_starknet_token` without NEAR ever authorizing deployment on chain B.
2. **Permanent DoS on legitimate deployment**: The only replay guard is `require(!isBridgeToken[nearToEthToken[metadata.token]], "ERR_TOKEN_EXIST")` (EVM) / `assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED')` (Starknet). Once the attacker's replay sets this mapping, NEAR can never deploy the token legitimately on chain B through the normal public flow. Recovery requires privileged `addCustomToken` / admin intervention.
3. **Bridge collateralization risk**: If NEAR's off-chain indexer auto-registers the replayed deployment and begins routing `finTransfer` messages to the attacker-deployed token address, tokens minted on chain B are backed by NEAR's signature but the deployment was never explicitly authorized for that chain, breaking the intended per-chain deployment governance.

This matches the allowed impact: **High — MPC signature verification bypass enabling unauthorized token deployment**. [8](#0-7) 

---

### Likelihood Explanation

- The attacker needs zero privileges: `deployToken` / `deploy_token` is a fully public, permissionless entry point on all three chains.
- The valid signature is permanently visible in the calldata of the original deployment transaction on chain A.
- The attack requires only a standard transaction submission on chain B — no special tooling, no front-running, no gas manipulation.
- The window is open from the moment the token is deployed on chain A until it is deployed on chain B, which for new chain expansions can be days or weeks.

---

### Recommendation

Include the destination chain ID in the `deployToken` / `deploy_token` Borsh-encoded message, exactly as `finTransfer` already does.

**EVM** (`OmniBridge.sol`):
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),          // destination chain binding
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

**Starknet** (`bridge_types.cairo`): Change `to_borsh(self: @MetadataPayload)` to `to_borsh(self: @MetadataPayload, chain_id: u8)` and prepend `chain_id` after the payload type byte. Pass `self.omni_bridge_chain_id.read()` at the call site in `deploy_token`.

**Solana** (`deploy_token.rs`): Write `SOLANA_OMNI_BRIDGE_CHAIN_ID` into the serialized buffer before the token fields, mirroring the pattern in `FinalizeTransferPayload::serialize_for_near`.

The NEAR MPC signing logic must be updated in lockstep to include the target chain ID when producing `deployToken` signatures.

---

### Proof of Concept

1. NEAR deploys token `"usdc.near"` on Ethereum. The `deployToken(sig, {token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6})` transaction is mined; `sig` is visible in calldata.

2. Attacker copies `sig` and the `MetadataPayload` verbatim.

3. Attacker calls `OmniBridge.deployToken(sig, payload)` on BSC's OmniBridge (or Starknet's `deploy_token`, or Solana's `deploy_token`).

4. The hash computed on BSC is:
   `keccak256( 0x01 | borsh("usdc.near") | borsh("USD Coin") | borsh("USDC") | 0x06 )`
   — identical to the Ethereum hash. `ECDSA.recover` returns `nearBridgeDerivedAddress`. Signature check passes.

5. A new `BridgeToken` is deployed on BSC. `nearToEthToken["usdc.near"]` is set to the attacker-triggered address. `isBridgeToken[that address] = true`.

6. NEAR later attempts to deploy `"usdc.near"` on BSC with a fresh, BSC-specific signature. The call reverts with `"ERR_TOKEN_EXIST"`. The token is permanently locked out of legitimate deployment on BSC.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L142-153)
```text
        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
            Borsh.encodeString(metadata.token),
            Borsh.encodeString(metadata.name),
            Borsh.encodeString(metadata.symbol),
            bytes1(metadata.decimals)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L155-158)
```text
        require(
            !isBridgeToken[nearToEthToken[metadata.token]],
            "ERR_TOKEN_EXIST"
        );
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

**File:** starknet/src/bridge_types.cairo (L61-71)
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
```

**File:** starknet/src/omni_bridge.cairo (L202-206)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);

```

**File:** solana/programs/bridge_token_factory/src/state/message/deploy_token.rs (L19-27)
```rust
    fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
        let mut writer = BufWriter::new(Vec::with_capacity(DEFAULT_SERIALIZER_CAPACITY));
        IncomingMessageType::Metadata.serialize(&mut writer)?;
        self.serialize(&mut writer)?; // borsh encoding
        writer
            .into_inner()
            .map_err(|_| error!(ErrorCode::InvalidArgs))
    }
}
```

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L29-36)
```rust
        // 3. token
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.0.serialize(&mut writer)?;
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. recipient
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.1.serialize(&mut writer)?;
```
