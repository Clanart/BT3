### Title
`deployToken` / `deploy_token` Signature Lacks Chain ID, Enabling Cross-Chain Replay to Permanently Corrupt Token Mappings — (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`)

---

### Summary

The NEAR MPC-signed payload for `deployToken` (EVM) and `deploy_token` (StarkNet) does not include the destination chain ID. Because both EVM and StarkNet verify the same Ethereum-style ECDSA signature against the same `nearBridgeDerivedAddress`, a valid signature obtained for one chain can be replayed verbatim on any other chain that shares the same borsh encoding format. This is the direct analog of the reported `fid`-omission bug: just as a removal signature not binding to a specific `fid` can be applied to the wrong `fid`, a `deployToken` signature not binding to a specific chain can be applied to the wrong chain.

---

### Finding Description

In `OmniBridge.sol`, `deployToken` constructs the signed payload as:

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
``` [1](#0-0) 

No `omniBridgeChainId` is present. Compare with `finTransfer`, which explicitly binds the payload to the destination chain:

```solidity
bytes1(omniBridgeChainId),   // destination chain bound here
Borsh.encodeAddress(payload.tokenAddress),
...
bytes1(omniBridgeChainId),   // and again for recipient chain
Borsh.encodeAddress(payload.recipient),
``` [2](#0-1) 

The identical omission exists on StarkNet. `MetadataPayload.to_borsh()` encodes only `PayloadType::Metadata`, token ID, name, symbol, and decimals — no chain ID:

```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    let mut borsh_bytes: ByteArray = Default::default();
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);
    borsh_bytes
}
``` [3](#0-2) 

Whereas `TransferMessagePayload.to_borsh(chain_id)` correctly binds to the chain:

```cairo
borsh_bytes.append_byte(chain_id);   // destination chain bound
borsh_bytes.append(@borsh::encode_address(*self.token_address));
``` [4](#0-3) 

Both EVM and StarkNet verify the signature against the same Ethereum-style derived address (`nearBridgeDerivedAddress` / `omni_bridge_derived_address`), using `ECDSA.recover` and `verify_eth_signature` respectively. [5](#0-4) 

Because the borsh encoding of `MetadataPayload` is identical on both chains and the verifying key is the same, a signature produced for chain A is cryptographically valid on chain B.

---

### Impact Explanation

An attacker who observes a valid `deployToken` signature on chain A (e.g., Ethereum) can immediately replay it on chain B (e.g., Arbitrum, or StarkNet) before NEAR's bridge operator deploys the token there legitimately. The consequences are:

1. **Permanent token-mapping corruption**: The token is registered in `nearToEthToken` / `near_to_starknet_token` on chain B without NEAR's authorization for that chain. There is no admin function in `OmniBridge.sol` to remove a bridge-token mapping once set (`removeCustomToken` only covers custom tokens), making this irreversible. [6](#0-5) 

2. **Permanent DoS on legitimate deployment**: When NEAR later attempts to deploy the same token on chain B, the `ERR_TOKEN_EXIST` / `ERR_TOKEN_ALREADY_DEPLOYED` guard rejects it. [7](#0-6) [8](#0-7) 

3. **Unauthorized token contract on chain B**: The deployed bridge token on chain B is a mintable/burnable contract controlled by the bridge. If NEAR's off-chain relayer later observes the token as "deployed" on chain B and begins signing `finTransfer` messages for it (e.g., in response to legitimate NEAR→chain-B transfer requests), tokens will be minted on chain B against a deployment that was never authorized for that chain, breaking the intended per-chain deployment governance.

---

### Likelihood Explanation

The attack requires only:
- Watching for a `DeployToken` event or a pending `deployToken` transaction on any supported chain.
- Calling `deployToken` (EVM) or `deploy_token` (StarkNet) on a different chain with the same `signatureData` and `metadata` arguments before the legitimate operator does.

No privileged access, leaked keys, or colluding MPC signers are needed. The signature is publicly visible on-chain the moment it is used on chain A. Any unprivileged user can perform the replay.

---

### Recommendation

Include `omniBridgeChainId` in the borsh-encoded metadata payload for `deployToken`, mirroring the pattern already used in `finTransfer`:

**EVM (`OmniBridge.sol`)**:
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),          // bind to destination chain
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

**StarkNet (`bridge_types.cairo`)**:
```cairo
fn to_borsh(self: @MetadataPayload, chain_id: u8) -> ByteArray {
    let mut borsh_bytes: ByteArray = Default::default();
    borsh_bytes.append_byte(PayloadType::Metadata.into());
+   borsh_bytes.append_byte(chain_id);   // bind to destination chain
    ...
}
```

The NEAR MPC signer must also include the target chain ID when producing the signature, so that a signature for chain A is cryptographically invalid on chain B.

---

### Proof of Concept

1. NEAR MPC signs `deployToken` for `usdc.near` on Ethereum (`omniBridgeChainId = 1`). The signature `sig` and payload `{token: "usdc.near", name: "USD Coin", symbol: "USDC", decimals: 6}` are broadcast in the Ethereum transaction.
2. Attacker observes the transaction and extracts `sig` and the payload.
3. Attacker calls `OmniBridge.deployToken(sig, payload)` on Arbitrum (`omniBridgeChainId = 2`) — the signature verification passes because the payload hash is identical (no chain ID in the hash).
4. `usdc.near` is now registered in Arbitrum's `nearToEthToken` mapping with a freshly deployed `BridgeToken` proxy, without NEAR's authorization for Arbitrum.
5. When NEAR's operator later calls `deployToken` for `usdc.near` on Arbitrum, the call reverts with `ERR_TOKEN_EXIST`, permanently blocking legitimate deployment.
6. The same replay works from EVM to StarkNet: calling `deploy_token(sig, payload)` on StarkNet passes `_verify_borsh_signature` because the borsh bytes and the verifying key are identical.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L120-127)
```text
    function removeCustomToken(
        address tokenAddress
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        delete isBridgeToken[tokenAddress];
        delete nearToEthToken[ethToNearToken[tokenAddress]];
        delete ethToNearToken[tokenAddress];
        delete customMinters[tokenAddress];
    }
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L289-298)
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

**File:** starknet/src/bridge_types.cairo (L61-68)
```text
    fn to_borsh(self: @TransferMessagePayload, chain_id: u8) -> ByteArray {
        let mut borsh_bytes: ByteArray = Default::default();
        borsh_bytes.append_byte(PayloadType::TransferMessage.into());
        borsh_bytes.append(@borsh::encode_u64(*self.destination_nonce));
        borsh_bytes.append_byte(*self.origin_chain);
        borsh_bytes.append(@borsh::encode_u64(*self.origin_nonce));
        borsh_bytes.append_byte(chain_id);
        borsh_bytes.append(@borsh::encode_address(*self.token_address));
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
