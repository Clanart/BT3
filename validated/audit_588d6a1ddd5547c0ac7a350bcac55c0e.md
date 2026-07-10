### Title
Unvalidated External Call in `logMetadata` Allows Arbitrary Wormhole Message Injection and Unauthorized Token Deployment on NEAR - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.logMetadata(address tokenAddress)` is a permissionless, payable function that calls `IERC20Metadata(tokenAddress).name()/.symbol()/.decimals()` on a caller-supplied address with no validation. In the `OmniBridgeWormhole` deployment, this triggers `logMetadataExtension`, which publishes a Wormhole `LogMetadata` message whose `tokenAddress`, `name`, `symbol`, and `decimals` fields are entirely attacker-controlled. Because the Wormhole VAA's `emitter_address` is the legitimate `OmniBridgeWormhole` contract, the NEAR bridge's factory-address check passes and it deploys a NEAR bridge token for the attacker-chosen EVM address.

---

### Finding Description

`logMetadata` at line 224 of `OmniBridge.sol` has no access control and performs no validation on `tokenAddress`:

```solidity
function logMetadata(address tokenAddress) external payable {
    string memory name = IERC20Metadata(tokenAddress).name();
    string memory symbol = IERC20Metadata(tokenAddress).symbol();
    uint8 decimals = IERC20Metadata(tokenAddress).decimals();
    logMetadataExtension(tokenAddress, name, symbol, decimals);
    emit BridgeTypes.LogMetadata(tokenAddress, name, symbol, decimals);
}
``` [1](#0-0) 

Contrast this with `deployToken` (line 135), which requires a valid MPC ECDSA signature over the metadata before proceeding, and `setMetadata` (line 204), which is gated by `onlyRole(DEFAULT_ADMIN_ROLE)`. `logMetadata` has neither guard. [2](#0-1) 

In `OmniBridgeWormhole`, the `logMetadataExtension` override publishes a Wormhole message whose payload is built entirely from the values returned by the attacker's contract:

```solidity
function logMetadataExtension(address tokenAddress, string memory name,
    string memory symbol, uint8 decimals) internal override {
    bytes memory payload = bytes.concat(
        bytes1(uint8(MessageType.LogMetadata)),
        bytes1(omniBridgeChainId),
        Borsh.encodeAddress(tokenAddress),
        Borsh.encodeString(name),
        Borsh.encodeString(symbol),
        bytes1(decimals)
    );
    _wormhole.publishMessage{value: msg.value}(wormholeNonce, payload, _consistencyLevel);
    wormholeNonce++;
}
``` [3](#0-2) 

The resulting Wormhole VAA carries `emitter_address = OmniBridgeWormhole` — the legitimate registered factory. On the NEAR side, `deploy_token_callback` checks exactly this field:

```rust
require!(
    self.factories.get(&chain) == Some(metadata.emitter_address),
    BridgeError::UnknownFactory.as_ref()
);
``` [4](#0-3) 

Because the message genuinely originates from the registered factory, the check passes and `deploy_token_internal` is called with the attacker-supplied `token_address`, `name`, `symbol`, and `decimals`, deploying a new NEAR bridge token for the fake EVM address. [5](#0-4) 

---

### Impact Explanation

An unprivileged attacker can:

1. **Deploy fake bridge tokens on NEAR for arbitrary EVM addresses** — including addresses they control. The NEAR bridge's only factory-authenticity check (`emitter_address == registered factory`) is satisfied because the message genuinely comes from `OmniBridgeWormhole`.
2. **Control the name, symbol, and decimals** of the deployed NEAR token, enabling impersonation of high-value tokens (USDC, WETH, etc.) to deceive users.
3. **Corrupt the NEAR token registry** — once a fake token is registered for an EVM address, legitimate registration of that address is blocked by the `TokenExists` guard in `deploy_token_internal`.
4. **Execute arbitrary code with `OmniBridge` as `msg.sender`** — the malicious contract's `name()`/`symbol()`/`decimals()` callbacks run with the asset-bearing bridge as the caller, enabling reentrancy into other bridge functions or exploitation of any protocol that grants special permissions to the bridge address.

This maps to the allowed High impact: **Wormhole verification bypass enabling unauthorized token deployment and message execution**. [6](#0-5) 

---

### Likelihood Explanation

- `logMetadata` is a documented, publicly advertised entry point (README, SDK, CLI) with no access control.
- The only cost to the attacker is the Wormhole message fee (`msg.value`), which is a small fixed amount.
- No privileged key, leaked secret, or colluding operator is required.
- The attack is fully executable by any EOA on any EVM chain where `OmniBridgeWormhole` is deployed.

---

### Recommendation

Add a validation check in `logMetadata` to ensure `tokenAddress` is not a bridge-deployed token and, more importantly, is a legitimate registered ERC20. The minimal fix mirrors the recommendation from M-08: probe the token registry before calling the external address.

```solidity
function logMetadata(address tokenAddress) external payable {
    require(!isBridgeToken[tokenAddress], "ERR_BRIDGE_TOKEN");
    // Optionally: require a whitelist or factory-registry check
    string memory name = IERC20Metadata(tokenAddress).name();
    string memory symbol = IERC20Metadata(tokenAddress).symbol();
    uint8 decimals = IERC20Metadata(tokenAddress).decimals();
    logMetadataExtension(tokenAddress, name, symbol, decimals);
    emit BridgeTypes.LogMetadata(tokenAddress, name, symbol, decimals);
}
```

A stronger fix would maintain an allowlist of EVM token addresses eligible for metadata logging, or require a governance/admin signature authorizing the `logMetadata` call for a given address, consistent with how `deployToken` requires an MPC signature. [1](#0-0) 

---

### Proof of Concept

1. Attacker deploys `MaliciousToken` on the same EVM chain as `OmniBridgeWormhole`:

```solidity
contract MaliciousToken {
    function name() external pure returns (string memory) { return "USD Coin"; }
    function symbol() external pure returns (string memory) { return "USDC"; }
    function decimals() external pure returns (uint8) { return 6; }
}
```

2. Attacker calls:

```solidity
OmniBridgeWormhole.logMetadata{value: wormholeFee}(address(MaliciousToken));
```

3. `OmniBridgeWormhole` calls `MaliciousToken.name()/.symbol()/.decimals()` with `OmniBridgeWormhole` as `msg.sender`, then publishes a Wormhole `LogMetadata` VAA with:
   - `tokenAddress = address(MaliciousToken)`
   - `name = "USD Coin"`, `symbol = "USDC"`, `decimals = 6`
   - `emitter_address = OmniBridgeWormhole` (the registered factory)

4. The NEAR bridge's `deploy_token` processes the VAA. The `emitter_address` check at `lib.rs:1159–1163` passes. `deploy_token_internal` deploys a NEAR token named "USD Coin" / "USDC" mapped to `address(MaliciousToken)` on EVM.

5. The legitimate USDC EVM address can no longer be registered (blocked by `TokenExists`). Users who bridge to the fake "USDC" token receive unbacked tokens. [7](#0-6) [8](#0-7)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L135-153)
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L224-232)
```text
    function logMetadata(address tokenAddress) external payable {
        string memory name = IERC20Metadata(tokenAddress).name();
        string memory symbol = IERC20Metadata(tokenAddress).symbol();
        uint8 decimals = IERC20Metadata(tokenAddress).decimals();

        logMetadataExtension(tokenAddress, name, symbol, decimals);

        emit BridgeTypes.LogMetadata(tokenAddress, name, symbol, decimals);
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L72-94)
```text
    function logMetadataExtension(
        address tokenAddress,
        string memory name,
        string memory symbol,
        uint8 decimals
    ) internal override {
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.LogMetadata)),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            Borsh.encodeString(name),
            Borsh.encodeString(symbol),
            bytes1(decimals)
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

**File:** near/omni-bridge/src/lib.rs (L1155-1163)
```rust
        let Ok(ProverResult::LogMetadata(metadata)) = call_result else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str());
        };

        let chain = metadata.emitter_address.get_chain();
        require!(
            self.factories.get(&chain) == Some(metadata.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L1165-1174)
```rust
        self.deploy_token_internal(
            chain,
            &metadata.token_address,
            BasicMetadata {
                name: metadata.name,
                symbol: metadata.symbol,
                decimals: metadata.decimals,
            },
            attached_deposit,
        )
```

**File:** near/omni-bridge/src/lib.rs (L2421-2424)
```rust
        require!(
            self.deployed_tokens.insert(&token_id),
            BridgeError::TokenExists.as_ref()
        );
```
