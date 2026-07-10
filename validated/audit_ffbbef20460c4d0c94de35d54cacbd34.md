### Title
Missing Zero-Address Validation for `nearBridgeDerivedAddress` Enables Signature Bypass or Permanent Bridge Freeze — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The `initialize()` function in `OmniBridge.sol` stores `nearBridgeDerivedAddress` — the sole authority used to verify MPC signatures — without validating it against `address(0)`. If the contract is deployed with a zero address (e.g., on a new network or during a redeployment), the signature check in `finTransfer()` and `deployToken()` is either bypassed entirely (allowing unauthorized token minting/release) or permanently broken (freezing all bridge settlement). The same structural flaw exists in the Starknet `OmniBridge` constructor for `omni_bridge_derived_address`.

---

### Finding Description

`OmniBridge.sol`'s `initialize()` accepts `nearBridgeDerivedAddress_` and stores it directly with no zero-address guard:

```solidity
function initialize(
    address tokenImplementationAddress_,
    address nearBridgeDerivedAddress_,   // ← no require(nearBridgeDerivedAddress_ != address(0))
    uint8 omniBridgeChainId_
) public initializer {
    tokenImplementationAddress = tokenImplementationAddress_;
    nearBridgeDerivedAddress = nearBridgeDerivedAddress_;   // stored as-is
    ...
}
``` [1](#0-0) 

This address is the **only** check standing between an arbitrary caller and token minting/release in both `finTransfer()` and `deployToken()`:

```solidity
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
``` [2](#0-1) [3](#0-2) 

The post-deployment setter `setNearBridgeDerivedAddress()` has the same omission:

```solidity
function setNearBridgeDerivedAddress(
    address nearBridgeDerivedAddress_
) external onlyRole(DEFAULT_ADMIN_ROLE) {
    nearBridgeDerivedAddress = nearBridgeDerivedAddress_;  // no zero check
}
``` [4](#0-3) 

The identical pattern exists in the Starknet contract's constructor, where `omni_bridge_derived_address` (an `EthAddress`) is written without a zero check and is then passed directly to `verify_eth_signature()` in `_verify_borsh_signature()`: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

Two concrete outcomes depending on the OpenZeppelin ECDSA version in use:

**Scenario A — Signature bypass (OZ ECDSA < 4.7.3, or any version where `ecrecover` returns `address(0)` for degenerate inputs):**  
When `nearBridgeDerivedAddress == address(0)`, an attacker supplies a signature whose `ecrecover` output is `address(0)` (achievable with `r = 0` or other degenerate inputs accepted by the precompile). The comparison `ECDSA.recover(...) != nearBridgeDerivedAddress` evaluates to `false`, so no revert occurs. The attacker can then:
- Call `finTransfer()` with arbitrary `recipient` and `amount` → unauthorized mint of any bridge token or release of locked native assets. **Critical: direct theft/unauthorized mint of bridged assets.**
- Call `deployToken()` with arbitrary metadata → unauthorized token deployment, poisoning the `nearToEthToken` mapping. **High: token-mapping corruption.**

**Scenario B — Permanent freeze (OZ ECDSA ≥ 4.7.3, which reverts on zero-address recovery):**  
Every call to `finTransfer()` and `deployToken()` reverts unconditionally because `ECDSA.recover()` itself panics before the comparison is reached. All in-flight cross-chain transfers become permanently unclaimable. **Critical: irrecoverable lock of user and protocol funds.**

---

### Likelihood Explanation

The external report explicitly identifies this as a realistic risk during **redeployments on new networks or new protocol versions** — exactly the scenario Omni Bridge faces as it expands to additional EVM chains (Arbitrum, Base, BNB, Polygon, HyperEVM, Abstract). Each new deployment is a fresh opportunity for a misconfigured `nearBridgeDerivedAddress_`. The absence of an on-chain guard means a deployment script error is the only trigger needed; no attacker action is required to create the vulnerable state, and only a single public call is needed to exploit it.

---

### Recommendation

Add a zero-address guard in `initialize()` and in `setNearBridgeDerivedAddress()`:

```solidity
function initialize(
    address tokenImplementationAddress_,
    address nearBridgeDerivedAddress_,
    uint8 omniBridgeChainId_
) public initializer {
    require(nearBridgeDerivedAddress_ != address(0), "ERR_ZERO_BRIDGE_ADDRESS");
    require(tokenImplementationAddress_ != address(0), "ERR_ZERO_TOKEN_IMPL");
    ...
}

function setNearBridgeDerivedAddress(
    address nearBridgeDerivedAddress_
) external onlyRole(DEFAULT_ADMIN_ROLE) {
    require(nearBridgeDerivedAddress_ != address(0), "ERR_ZERO_BRIDGE_ADDRESS");
    nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
}
```

Apply the same fix to the Starknet constructor:

```cairo
fn constructor(..., omni_bridge_derived_address: EthAddress, ...) {
    assert(!omni_bridge_derived_address.is_zero(), 'ERR_ZERO_BRIDGE_ADDRESS');
    self.omni_bridge_derived_address.write(omni_bridge_derived_address);
    ...
}
```

---

### Proof of Concept

**Setup**: Deploy `OmniBridge` (or its chain-specific subclass) with `nearBridgeDerivedAddress_ = address(0)`.

**Attack (Scenario A)**:

```solidity
// Craft a signature where ecrecover returns address(0)
// r=0, s=1, v=27 causes the ecrecover precompile to return 0x000...000
bytes memory zeroSig = abi.encodePacked(bytes32(0), bytes32(uint256(1)), uint8(27));

OmniBridge bridge = OmniBridge(deployedAddress);

BridgeTypes.TransferMessagePayload memory payload = BridgeTypes.TransferMessagePayload({
    destinationNonce: 1,
    originChain: 1,
    originNonce: 1,
    tokenAddress: address(targetBridgeToken),   // any bridge token
    amount: 1_000_000e18,                        // arbitrary amount
    recipient: attacker,
    feeRecipient: "",
    message: ""
});

// nearBridgeDerivedAddress == address(0), ECDSA.recover returns address(0) → check passes
bridge.finTransfer(zeroSig, payload);
// attacker receives 1,000,000 tokens minted from thin air
```

The `completedTransfers[1]` nonce is consumed, but the attacker can repeat with nonce 2, 3, … for each desired token or amount, draining the bridge's collateral backing.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L72-86)
```text
    function initialize(
        address tokenImplementationAddress_,
        address nearBridgeDerivedAddress_,
        uint8 omniBridgeChainId_
    ) public initializer {
        tokenImplementationAddress = tokenImplementationAddress_;
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
        omniBridgeChainId = omniBridgeChainId_;

        __UUPSUpgradeable_init();
        __AccessControl_init();
        __Pausable_init_unchained();
        _grantRole(DEFAULT_ADMIN_ROLE, _msgSender());
        _grantRole(PAUSABLE_ADMIN_ROLE, _msgSender());
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L151-153)
```text
        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L311-313)
```text
        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L568-572)
```text
    function setNearBridgeDerivedAddress(
        address nearBridgeDerivedAddress_
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
    }
```

**File:** starknet/src/omni_bridge.cairo (L122-139)
```text
    #[constructor]
    fn constructor(
        ref self: ContractState,
        omni_bridge_derived_address: EthAddress,
        omni_bridge_chain_id: u8,
        token_class_hash: ClassHash,
        default_admin: ContractAddress,
        strk_token_address: ContractAddress,
    ) {
        self.omni_bridge_derived_address.write(omni_bridge_derived_address);
        self.omni_bridge_chain_id.write(omni_bridge_chain_id);
        self.bridge_token_class_hash.write(token_class_hash);
        self.strk_token_address.write(strk_token_address);
        self.pause_flags.write(0);

        self.accesscontrol.initializer();
        self.accesscontrol._grant_role(DEFAULT_ADMIN_ROLE, default_admin);
    }
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
