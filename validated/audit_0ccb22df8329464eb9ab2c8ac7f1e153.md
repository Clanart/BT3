### Title
Missing Chain-ID Domain Separation in `deployToken` Signature Allows Cross-Chain Replay of MPC-Signed Token Deployment — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The `deployToken` function in `OmniBridge.sol` verifies an MPC-produced ECDSA signature over a payload that contains only token metadata (`token`, `name`, `symbol`, `decimals`). No `omniBridgeChainId` or contract address is included in the signed message. In contrast, `finTransfer` correctly binds its signature to the destination chain via `omniBridgeChainId`. Because the metadata signature is chain-agnostic, any attacker who observes a valid `LogMetadataEvent` on NEAR can replay the same signature on every other EVM chain that has an `OmniBridgeWormhole` deployment, causing unauthorized token deployment and forcing the NEAR bridge to register the token on unintended chains.

---

### Finding Description

**Root cause — `OmniBridge.sol` `deployToken` (lines 142–153):**

The Borsh-encoded payload that is hashed and verified contains no chain discriminator:

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

Compare with `finTransfer`, which correctly embeds `omniBridgeChainId` twice (destination chain and recipient chain):

```solidity
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.tokenAddress),
Borsh.encodeUint128(payload.amount),
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.recipient),
``` [2](#0-1) 

The NEAR side (`log_metadata_callback`) also produces the metadata payload without any chain binding:

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
``` [3](#0-2) 

The resulting signature is emitted publicly in a `LogMetadataEvent` and is therefore observable by any party.

**Propagation via Wormhole — `OmniBridgeWormhole.sol` `deployTokenExtension` (lines 48–70):**

When `deployToken` succeeds on any EVM chain, `deployTokenExtension` publishes a Wormhole message containing the token address and chain ID. The NEAR bridge processes this message in `bind_token_callback`, which registers the token in `token_id_to_address`, `token_address_to_id`, `token_decimals`, and initialises a `locked_tokens` entry for the new chain — all without any check that the deployment was authorised for that specific chain. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

An attacker who replays a valid metadata signature on an unintended EVM chain (e.g., BSC when the signature was issued for Ethereum) causes:

1. **Unauthorized token deployment** on Chain B — the `isBridgeToken` / `nearToEthToken` mappings are set on Chain B's OmniBridge without NEAR's explicit authorisation.
2. **Forced NEAR-side registration** — the Wormhole message triggers `bind_token_callback` on NEAR, which inserts a `locked_tokens` entry initialised to `0` for Chain B. The NEAR bridge now treats Chain B as a valid destination for that token.
3. **Permanent blocking of legitimate deployment** — because `nearToEthToken[metadata.token]` is already set on Chain B, any subsequent legitimate `deployToken` call for the same token on Chain B reverts with `ERR_TOKEN_EXIST`, permanently preventing the official deployment at the intended address.
4. **Accounting corruption** — the NEAR bridge's `locked_tokens` tracking for Chain B starts at `0` regardless of how many tokens are already in circulation on Chain A, breaking the collateralisation invariant if the bridge relies on per-chain locked-token accounting to bound minting.

This matches the allowed impact: **High — signature/MPC verification bypass enabling unauthorized token deployment and accounting corruption**.

---

### Likelihood Explanation

- The MPC signature is emitted as a public NEAR event (`LogMetadataEvent`) immediately after `log_metadata_callback` succeeds. No privileged access is needed to observe it.
- Calling `deployToken` on a second EVM chain requires only the signature bytes and the metadata struct — both are fully public.
- The attack is viable whenever two EVM chains share the same `nearBridgeDerivedAddress` (the MPC key), which is the intended design.
- No colluding MPC signers, no leaked keys, and no chain-level attack assumptions are required.

---

### Recommendation

Include `omniBridgeChainId` in the Borsh-encoded metadata payload that the MPC signs, mirroring the pattern already used in `finTransfer`:

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

The NEAR side's `MetadataPayload` and `log_metadata_callback` must be updated to include the target chain ID in the signed payload correspondingly, so that a signature issued for Ethereum cannot be accepted by BSC's OmniBridge.

---

### Proof of Concept

1. NEAR bridge operator calls `log_metadata(usdc.near)` on the NEAR bridge.
2. `log_metadata_callback` fires, MPC signs `keccak256(borsh(MetadataPayload{Metadata, "usdc.near", "USD Coin", "USDC", 6}))`, and emits `LogMetadataEvent{signature, metadata_payload}`.
3. Legitimate relayer calls `OmniBridgeWormhole(ethereum).deployToken(sig, metadata)` → USDC deployed at `0xAAA...` on Ethereum; Wormhole message published; NEAR bridge registers USDC on Ethereum.
4. Attacker copies `sig` from the NEAR event log and calls `OmniBridgeWormhole(bsc).deployToken(sig, metadata)` — the same `keccak256` hash is produced on BSC because `omniBridgeChainId` is absent; `ECDSA.recover` returns `nearBridgeDerivedAddress`; signature check passes.
5. USDC is deployed at `0xBBB...` on BSC; BSC's OmniBridge publishes a Wormhole message.
6. NEAR bridge processes the Wormhole message via `bind_token` → `bind_token_callback`; `token_id_to_address[(Bsc, usdc.near)] = 0xBBB...` is inserted; `locked_tokens[(Bsc, usdc.near)] = 0` is initialised.
7. Any future attempt by the legitimate operator to officially deploy USDC on BSC reverts with `ERR_TOKEN_EXIST` on BSC's OmniBridge, permanently blocking the intended deployment.

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L294-298)
```text
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
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

**File:** near/omni-bridge/src/lib.rs (L1249-1300)
```rust
        let Ok(ProverResult::DeployToken(deploy_token)) = call_result else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str());
        };

        require!(
            self.factories
                .get(&deploy_token.emitter_address.get_chain())
                == Some(deploy_token.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let storage_usage = env::storage_usage();

        self.add_token(
            &deploy_token.token,
            &deploy_token.token_address,
            deploy_token.decimals,
            deploy_token.origin_decimals,
        );

        require!(
            self.locked_tokens
                .insert(
                    &(
                        deploy_token.token_address.get_chain(),
                        deploy_token.token.clone(),
                    ),
                    &0,
                )
                .is_none(),
            TokenLockError::TokenAlreadyLocked.as_ref()
        );

        let required_deposit = env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into());

        require!(
            attached_deposit >= required_deposit,
            BridgeError::InsufficientStorageDeposit.as_ref()
        );

        env::log_str(
            &OmniBridgeEvent::BindTokenEvent {
                token_id: deploy_token.token,
                token_address: deploy_token.token_address,
                decimals: deploy_token.decimals,
                origin_decimals: deploy_token.origin_decimals,
            }
            .to_log_string(),
        );

        attached_deposit.saturating_sub(required_deposit)
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L48-70)
```text
    function deployTokenExtension(
        string memory token,
        address tokenAddress,
        uint8 decimals,
        uint8 originDecimals
    ) internal override {
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.DeployToken)),
            Borsh.encodeString(token),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            bytes1(decimals),
            bytes1(originDecimals)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

        wormholeNonce++;
    }
```
