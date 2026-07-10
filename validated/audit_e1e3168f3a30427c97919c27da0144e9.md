### Title
`deployToken` Signed Payload Lacks Chain-ID Binding, Enabling Cross-Chain Signature Replay — (Files: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`, `starknet/src/bridge_types.cairo`)

---

### Summary

The MPC-signed payload used to authorize token deployment (`deployToken` / `deploy_token`) contains no chain identifier or contract address. The byte-for-byte identical serialization format across EVM, Solana, and StarkNet means a single MPC signature authorizing deployment on one chain is unconditionally valid on every other chain. Any observer can replay the signature to deploy the same token on any chain where it has not yet been registered, without NEAR's authorization.

---

### Finding Description

**EVM** (`OmniBridge.sol`, `deployToken`, lines 142–149):

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),  // 0x01
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
bytes32 hashed = keccak256(borshEncoded);
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress)
    revert InvalidSignature();
``` [1](#0-0) 

**Solana** (`deploy_token.rs`, `serialize_for_near`, lines 19–26):

```rust
IncomingMessageType::Metadata.serialize(&mut writer)?;  // 0x01
self.serialize(&mut writer)?;  // token, name, symbol, decimals
``` [2](#0-1) 

**StarkNet** (`bridge_types.cairo`, `MetadataPayloadImpl::to_borsh`, lines 36–44):

```cairo
borsh_bytes.append_byte(PayloadType::Metadata.into());  // 0x01
borsh_bytes.append(@borsh::encode_byte_array(self.token));
borsh_bytes.append(@borsh::encode_byte_array(self.name));
borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
borsh_bytes.append_byte(*self.decimals);
``` [3](#0-2) 

All three produce the identical byte sequence:

```
[0x01] | borsh_u32_le(len(token)) | token_bytes
       | borsh_u32_le(len(name))  | name_bytes
       | borsh_u32_le(len(symbol))| symbol_bytes
       | [decimals]
```

No chain ID, no contract address, no program ID is mixed in.

**Contrast with `finTransfer`**, which correctly embeds `omniBridgeChainId` twice (for token chain and recipient chain):

```solidity
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.tokenAddress),
...
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.recipient),
``` [4](#0-3) 

`deployToken` has no equivalent binding.

---

### Impact Explanation

**Unauthorized cross-chain token deployment (High).**

1. NEAR's MPC signs a `deployToken` payload for token `usdc.near` on Ethereum (chain ID 0). The signature covers only `[0x01 | "usdc.near" | "USD Coin" | "USDC" | 6]`.
2. An attacker observes the Ethereum transaction and replays the identical `(payload, signature)` on Arbitrum, Base, BNB, StarkNet, and Solana — all chains where the token is not yet registered.
3. Each target chain's bridge contract accepts the signature (same signer, same hash), deploys a new `BridgeToken` proxy, and writes `isBridgeToken[proxy] = true`, `nearToEthToken["usdc.near"] = proxy`, `ethToNearToken[proxy] = "usdc.near"`.
4. The token is now permanently registered on those chains without NEAR's authorization.
5. Consequence A — **Blocked legitimate deployment**: When NEAR later tries to officially deploy `usdc.near` on Arbitrum, `deployToken` reverts with `ERR_TOKEN_EXIST` because `isBridgeToken[nearToEthToken["usdc.near"]]` is already true. The legitimate deployment path is permanently blocked.
6. Consequence B — **User fund lock**: A user who calls `initTransfer` on the replay-deployed Arbitrum token (burning/locking funds) expects NEAR to finalize the transfer. NEAR's bridge has no record of authorizing this deployment and will not sign a `finTransfer` for it. The user's funds are irrecoverably locked. [5](#0-4) 

---

### Likelihood Explanation

**High.** The attack requires only:
- Observing any on-chain `deployToken` transaction (public mempool / block explorer).
- Calling `deployToken` on any other chain with the same calldata.

No privileged access, no leaked keys, no MPC collusion. Any unprivileged user can execute this immediately after the first legitimate deployment on any chain.

---

### Recommendation

Include the destination chain identifier in the signed payload for `deployToken`, mirroring the pattern already used in `finTransfer`. For EVM:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // <-- add this
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Apply the equivalent fix in `deploy_token.rs` (Solana) and `bridge_types.cairo` (StarkNet) by prepending `SOLANA_OMNI_BRIDGE_CHAIN_ID` / `omni_bridge_chain_id` before the token string. The NEAR signing side must include the same chain ID when constructing the payload to sign.

---

### Proof of Concept

1. NEAR MPC signs `deployToken` for `usdc.near` on Ethereum. Transaction is broadcast and mined. The `(signatureData, metadata)` calldata is public.

2. Attacker calls `OmniBridge.deployToken(signatureData, metadata)` on the Arbitrum `OmniBridge` contract with the identical arguments.

3. Arbitrum's `OmniBridge` computes:
   ```
   keccak256([0x01 | "usdc.near" | "USD Coin" | "USDC" | 6])
   ```
   This is byte-for-byte identical to what Ethereum computed. `ECDSA.recover` returns `nearBridgeDerivedAddress`. Signature check passes.

4. A new `BridgeToken` proxy is deployed on Arbitrum. `isBridgeToken[proxy] = true`, `nearToEthToken["usdc.near"] = proxy`.

5. NEAR's official `deployToken` call for Arbitrum later reverts: `require(!isBridgeToken[nearToEthToken[metadata.token]], "ERR_TOKEN_EXIST")`. [6](#0-5)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L155-194)
```text
        require(
            !isBridgeToken[nearToEthToken[metadata.token]],
            "ERR_TOKEN_EXIST"
        );
        uint8 decimals = _normalizeDecimals(metadata.decimals);

        // slither-disable-next-line reentrancy-no-eth
        address bridgeTokenProxy = address(
            new ERC1967Proxy(
                tokenImplementationAddress,
                abi.encodeWithSelector(
                    BridgeToken.initialize.selector,
                    metadata.name,
                    metadata.symbol,
                    decimals
                )
            )
        );

        deployTokenExtension(
            metadata.token,
            bridgeTokenProxy,
            decimals,
            metadata.decimals
        );

        emit BridgeTypes.DeployToken(
            bridgeTokenProxy,
            metadata.token,
            metadata.name,
            metadata.symbol,
            decimals,
            metadata.decimals
        );

        isBridgeToken[address(bridgeTokenProxy)] = true;
        ethToNearToken[address(bridgeTokenProxy)] = metadata.token;
        nearToEthToken[metadata.token] = address(bridgeTokenProxy);

        return bridgeTokenProxy;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L294-298)
```text
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
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
