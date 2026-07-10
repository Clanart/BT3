The vulnerability is real. Let me confirm the exact payload construction and compare it to `finTransfer` to establish the missing binding.

**`deployToken` signed payload** (`OmniBridge.sol` lines 142–149):
```
PayloadType.Metadata || token || name || symbol || decimals
```

**`finTransfer` signed payload** (`OmniBridge.sol` lines 289–308):
```
PayloadType.TransferMessage || destinationNonce || originChain || originNonce
  || omniBridgeChainId || tokenAddress || amount || omniBridgeChainId || recipient || ...
```

`finTransfer` binds to `omniBridgeChainId` and `tokenAddress`. `deployToken` binds to **none** of those. The `omniBridgeChainId` field stored in the contract is never included in the metadata hash.

---

### Title
Cross-Deployment Replay of `deployToken` MPC Signature Due to Missing Chain/Contract Binding — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
The Borsh-encoded payload hashed and ECDSA-verified in `deployToken` contains only `(PayloadType.Metadata, token, name, symbol, decimals)`. It omits the contract address, EVM chain ID, and `omniBridgeChainId`. Because every OmniBridge deployment across all EVM chains shares the same `nearBridgeDerivedAddress` (the MPC-derived Ethereum address is chain-agnostic), a signature produced for deployment A is cryptographically valid on deployment B.

### Finding Description
In `OmniBridge.deployToken`, the message that is signed and verified is: [1](#0-0) 

The five fields encoded are `PayloadType.Metadata`, `token`, `name`, `symbol`, and `decimals`. No contract address, no EVM chain ID, and no `omniBridgeChainId` is included. The contract stores `omniBridgeChainId` as a state variable: [2](#0-1) 

but never incorporates it into the metadata hash. By contrast, `finTransfer` correctly binds to `omniBridgeChainId` twice: [3](#0-2) 

The `nearBridgeDerivedAddress` is the Ethereum address recovered from the MPC public key on NEAR. Because the MPC key is chain-agnostic, every OmniBridge deployment on every EVM chain uses the **same** `nearBridgeDerivedAddress`. The precondition stated in the question ("two deployments share the same `nearBridgeDerivedAddress`") is therefore satisfied by the normal production deployment topology — it is not a special or unlikely configuration.

The only replay guard in `deployToken` is: [4](#0-3) 

This check only prevents re-use of the same `metadata.token` string **within the same contract instance**. It provides no protection against replaying the signature on a different contract instance.

### Impact Explanation
An unprivileged attacker who observes a valid `deployToken` call on deployment A (e.g., Ethereum mainnet) can immediately replay the identical `(signatureData, metadata)` tuple on deployment B (e.g., Base, Arbitrum, or any second deployment on the same chain). The result:

1. A wrapped token for the NEAR asset is deployed on deployment B without the NEAR bridge or its operators ever authorizing that specific deployment.
2. The `nearToEthToken` and `isBridgeToken` mappings on deployment B are populated, and the `ERR_TOKEN_EXIST` guard permanently blocks any future legitimate deployment of that token on deployment B.
3. The canonical token address on deployment B is hijacked — it is controlled by the attacker's transaction ordering, not by the bridge operators.

Regarding unbacked minting: `finTransfer` is chain-specific (includes `omniBridgeChainId`), so the attacker cannot directly mint tokens on deployment B using replayed `finTransfer` signatures from deployment A **if the two deployments have different `omniBridgeChainId` values**. However, if two deployments on the same chain share the same `omniBridgeChainId` (e.g., a v1 and v2 deployment, or a test and production deployment on the same network), then `finTransfer` signatures are also replayable, enabling unbacked minting. Even without that, the unauthorized token deployment itself constitutes a High-severity signature verification bypass enabling unauthorized token deployment, as defined in the allowed impact scope.

### Likelihood Explanation
- The precondition is always true in production: all EVM OmniBridge deployments share the same `nearBridgeDerivedAddress`.
- The attack requires no privileges: the attacker only needs to observe a public on-chain transaction.
- The attack is front-runnable: the attacker can watch the mempool on chain A and submit the replay on chain B before the legitimate transaction is even confirmed.
- No special tooling is needed beyond a standard Ethereum wallet.

### Recommendation
Include `omniBridgeChainId` and `address(this)` (the contract address) in the Borsh-encoded metadata payload before hashing:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // chain binding
    Borsh.encodeAddress(address(this)), // contract binding
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

The NEAR side (`log_metadata_callback`) must include the same fields when constructing the `MetadataPayload` sent to the MPC signer: [5](#0-4) 

### Proof of Concept
1. Deploy two `OmniBridgeWormhole` instances (`bridgeA`, `bridgeB`) on the same or different EVM chains, both initialized with the same `nearBridgeDerivedAddress` (the MPC-derived address).
2. Produce a valid `deployToken` signature for `bridgeA` for NEAR token `"token.near"` with name/symbol/decimals `("Token", "TKN", 18)`.
3. Call `bridgeA.deployToken(sig, metadata)` — succeeds, token deployed at address `addrA`.
4. Call `bridgeB.deployToken(sig, metadata)` with the **identical** `sig` and `metadata` — succeeds, token deployed at address `addrB`.
5. Assert that both calls returned non-zero addresses and that `bridgeB.nearToEthToken("token.near") != address(0)`.
6. Attempt a second legitimate `bridgeB.deployToken` call — reverts with `ERR_TOKEN_EXIST`, confirming the slot is permanently hijacked.

The second call in step 4 should revert if the fix is applied, because the hash would include `bridgeB`'s address and chain ID, which differ from what was signed for `bridgeA`.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L42-42)
```text
    uint8 public omniBridgeChainId;
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

**File:** near/omni-bridge/src/lib.rs (L341-360)
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

        ext_signer::ext(self.mpc_signer.clone())
            .with_static_gas(MPC_SIGNING_GAS)
            .with_attached_deposit(env::attached_deposit())
            .sign(SignRequest {
                payload,
                path: SIGN_PATH.to_owned(),
                key_version: 0,
            })
```
