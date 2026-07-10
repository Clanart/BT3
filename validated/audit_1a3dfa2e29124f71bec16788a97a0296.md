### Title
Missing Chain ID in `deployToken` Signed Message Enables Cross-Chain Signature Replay - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The `deployToken` / `deploy_token` functions across EVM, Starknet, and Solana bridge contracts verify an MPC-produced signature over a `MetadataPayload` that does **not** include the destination chain ID. Because the same NEAR MPC key (`"bridge-1"`) derives the same `nearBridgeDerivedAddress` on every chain, a single signature obtained for deploying a token on one chain is cryptographically valid on every other chain simultaneously. Any observer of the NEAR event log can replay the signature to deploy an unauthorized bridge token on chains where the NEAR bridge never intended to authorize deployment.

---

### Finding Description

When NEAR's `log_metadata_callback` signs a `MetadataPayload`, the borsh-encoded payload contains only:

```
PayloadType::Metadata | token (string) | name | symbol | decimals
``` [1](#0-0) 

No destination chain ID is mixed into the payload before it is submitted to the MPC signer.

On the EVM side, `deployToken` reconstructs and verifies the same chain-agnostic encoding:

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
``` [2](#0-1) 

The Starknet `MetadataPayload.to_borsh()` is identical — no chain ID field:

```cairo
borsh_bytes.append_byte(PayloadType::Metadata.into());
borsh_bytes.append(@borsh::encode_byte_array(self.token));
borsh_bytes.append(@borsh::encode_byte_array(self.name));
borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
borsh_bytes.append_byte(*self.decimals);
``` [3](#0-2) 

The Solana `DeployTokenPayload.serialize_for_near()` is the same:

```rust
IncomingMessageType::Metadata.serialize(&mut writer)?;
self.serialize(&mut writer)?; // token, name, symbol, decimals only
``` [4](#0-3) 

**Contrast with `finTransfer`**, which correctly binds the signature to the destination chain by including `omniBridgeChainId` twice (for token address and recipient address fields):

```solidity
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.tokenAddress),
...
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.recipient),
``` [5](#0-4) 

Starknet's `TransferMessagePayload.to_borsh(chain_id)` similarly includes `chain_id`: [6](#0-5) 

The `nearBridgeDerivedAddress` / `omni_bridge_derived_address` / `derived_near_bridge_address` is derived from the same NEAR MPC key path `"bridge-1"` on every chain: [7](#0-6) 

Because the signer key is identical across all chains and the signed payload contains no chain discriminator, a `deployToken` signature is valid on Ethereum, Arbitrum, Base, BNB, Polygon, HyperEVM, Abstract, Starknet, and Solana simultaneously.

---

### Impact Explanation

**High — Proof/signature verification bypass enabling unauthorized token deployment.**

An attacker who observes the `LogMetadataEvent` emitted on NEAR (a public event containing the MPC signature and metadata payload) can:

1. Extract the signature from the NEAR event log.
2. Submit it to `deployToken` on any EVM chain, Starknet, or Solana — chains where the token owner never initiated a `log_metadata` flow.
3. The bridge contract on each target chain accepts the signature as valid, registers the token in its `nearToEthToken` / `near_to_starknet_token` mapping, and mints a new bridge token contract.

This creates unauthorized wrapped token instances on chains the token owner did not authorize. Once deployed, these tokens are indistinguishable from legitimately deployed bridge tokens and can be used in `finTransfer` flows to receive bridged assets. If the NEAR token's metadata changes after the original signature was issued, the replayed deployment also creates a bridge token with stale/incorrect name or symbol, corrupting the token-mapping accounting. [8](#0-7) [9](#0-8) 

---

### Likelihood Explanation

**High.** The NEAR `sign_log_metadata_callback` emits the signature as a public on-chain event:

```rust
env::log_str(
    &OmniBridgeEvent::LogMetadataEvent {
        signature,
        metadata_payload,
    }
    .to_log_string(),
);
``` [10](#0-9) 

Any party monitoring NEAR can extract the signature with zero additional privilege. No key compromise, no colluding MPC signers, and no special access are required. The attacker only needs to call `deployToken` on the target chain with the observed signature and matching payload.

---

### Recommendation

Include the destination chain ID in the `MetadataPayload` borsh encoding before it is submitted to the MPC signer, mirroring the pattern already used in `TransferMessagePayload`. On the NEAR side, `log_metadata_callback` should accept a `destination_chain: ChainKind` parameter and encode it into the payload. Each chain's `deployToken` function should then verify that the chain ID in the signed payload matches its own `omniBridgeChainId`.

---

### Proof of Concept

1. Token `token.near` calls `log_metadata("token.near")` on NEAR, targeting Ethereum deployment.
2. NEAR MPC signs `keccak256(borsh(Metadata | "token.near" | "Token" | "TKN" | 18))` → `sig_S`.
3. `sig_S` is emitted in the NEAR `LogMetadataEvent` (publicly observable).
4. Attacker submits `deployToken(sig_S, {token: "token.near", name: "Token", symbol: "TKN", decimals: 18})` to the Arbitrum `OmniBridge`.
5. `ECDSA.recover(keccak256(borsh(...)), sig_S) == nearBridgeDerivedAddress` — passes.
6. `isBridgeToken[nearToEthToken["token.near"]]` is false on Arbitrum — passes.
7. A new `BridgeToken` proxy is deployed on Arbitrum and registered: `nearToEthToken["token.near"] = <new_arb_token>`.
8. Repeat for Base, BNB, Polygon, Starknet, Solana using the same `sig_S`. [11](#0-10) [12](#0-11) [13](#0-12)

### Citations

**File:** near/omni-bridge/src/lib.rs (L84-84)
```rust
const SIGN_PATH: &str = "bridge-1";
```

**File:** near/omni-bridge/src/lib.rs (L341-351)
```rust
        let metadata_payload = MetadataPayload {
            prefix: PayloadType::Metadata,
            token: token_id.to_string(),
            name: metadata.name,
            symbol: metadata.symbol,
            decimals: metadata.decimals,
        };

        let payload = near_sdk::env::keccak256_array(
            borsh::to_vec(&metadata_payload).near_expect(BridgeError::Borsh),
        );
```

**File:** near/omni-bridge/src/lib.rs (L376-383)
```rust
            env::log_str(
                &OmniBridgeEvent::LogMetadataEvent {
                    signature,
                    metadata_payload,
                }
                .to_log_string(),
            );
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L135-195)
```text
    function deployToken(
        bytes calldata signatureData,
        BridgeTypes.MetadataPayload calldata metadata
    ) external payable whenNotPaused(PAUSED_DEPLOY_TOKEN) returns (address) {
        if (tokenImplementationAddress == address(0)) {
            revert TokenImplementationNotSet();
        }
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
    }
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

**File:** solana/programs/bridge_token_factory/src/state/message/deploy_token.rs (L16-27)
```rust
impl Payload for DeployTokenPayload {
    type AdditionalParams = ();

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

**File:** starknet/src/omni_bridge.cairo (L202-240)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);

            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');

            let decimals = _normalizeDecimals(payload.decimals);

            let mut constructor_calldata: Array<felt252> = array![];
            (payload.name.clone(), payload.symbol.clone(), decimals)
                .serialize(ref constructor_calldata);

            // Use the low part of the u256 hash to ensure it fits in felt252
            let salt: felt252 = token_id_hash.low.into();
            let (contract_address, _) = deploy_syscall(
                self.bridge_token_class_hash.read(), salt, constructor_calldata.span(), false,
            )
                .unwrap_syscall();

            self.starknet_to_near_token.write(contract_address, payload.token.clone());
            self.near_to_starknet_token.write(token_id_hash, contract_address);

            self
                .emit(
                    Event::DeployToken(
                        DeployToken {
                            token_address: contract_address,
                            near_token_id: payload.token,
                            name: payload.name,
                            symbol: payload.symbol,
                            decimals,
                            origin_decimals: payload.decimals,
                        },
                    ),
                )
        }
```
