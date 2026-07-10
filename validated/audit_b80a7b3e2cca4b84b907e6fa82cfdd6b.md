### Title
Missing `block.chainid` Domain Separation in `finTransfer` Signature Allows Hard-Fork Replay — (`File: evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.finTransfer` verifies an MPC-produced ECDSA signature over a Borsh-encoded payload that includes `omniBridgeChainId` (a static `uint8` storage variable) but never includes `block.chainid`. Because `omniBridgeChainId` is a storage value copied verbatim to any hard-forked chain, a valid MPC signature for a pending `finTransfer` on the original chain is equally valid on the forked chain, enabling a recipient to claim the same bridged tokens twice.

---

### Finding Description

`OmniBridge.finTransfer` constructs the signed message as follows:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
    Borsh.encodeUint64(payload.destinationNonce),
    bytes1(payload.originChain),
    Borsh.encodeUint64(payload.originNonce),
    bytes1(omniBridgeChainId),          // ← static uint8, NOT block.chainid
    Borsh.encodeAddress(payload.tokenAddress),
    Borsh.encodeUint128(payload.amount),
    bytes1(omniBridgeChainId),          // ← static uint8, NOT block.chainid
    Borsh.encodeAddress(payload.recipient),
    ...
);
bytes32 hashed = keccak256(borshEncoded);
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
``` [1](#0-0) 

`omniBridgeChainId` is a `uint8` set once at `initialize` time and never updated: [2](#0-1) [3](#0-2) 

A grep across the entire `evm/src/` tree confirms `block.chainid` is referenced **nowhere** in any contract. 

Replay protection relies solely on the `completedTransfers` nonce bitmap: [4](#0-3) 

At the moment of a hard fork, the entire contract storage — including `omniBridgeChainId`, `completedTransfers`, and `nearBridgeDerivedAddress` — is duplicated identically on both chains. Any MPC signature that was produced but not yet submitted (i.e., whose `destinationNonce` is absent from `completedTransfers`) is therefore valid on both the original chain and the fork, because the signed digest is byte-for-byte identical on both.

---

### Impact Explanation

An attacker (or the legitimate recipient) who holds an unsubmitted MPC-signed `finTransfer` payload at the time of a hard fork can submit it on **both** chains. The result is:

- On the original chain: tokens are minted/released to the recipient as intended.
- On the forked chain: the same tokens are minted/released again from the same locked collateral, creating unbacked supply or draining the bridge vault.

This matches the **High** impact category: *cross-chain replay / duplicate settlement enabling double-spend or unbacked supply*.

---

### Likelihood Explanation

Hard forks are rare but not unprecedented (Ethereum Classic, ETH/ETC split, Ethereum PoW fork). The window of exploitability is any MPC-signed payload that exists off-chain but has not yet been submitted on-chain at the moment the fork occurs. Because `finTransfer` is a permissionless function callable by anyone, the attacker does not need to be the original recipient — they only need to possess the signed payload (e.g., obtained from a public relayer or mempool).

---

### Recommendation

Include `block.chainid` in the signed payload so that a signature produced for one chain is cryptographically invalid on any other chain (including forks):

```solidity
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
    Borsh.encodeUint256(block.chainid),   // ← add EVM chain ID
    ...
);
```

The same fix should be applied to `deployToken`, whose signed payload currently contains **no** chain identifier at all: [5](#0-4) 

---

### Proof of Concept

1. NEAR-side: user initiates a transfer; MPC produces a signed `finTransfer` payload `P` with `destinationNonce = N`.
2. Before `P` is submitted on-chain, a hard fork occurs at block `F`. Both chains now share identical state: `completedTransfers[N] == false`, `omniBridgeChainId == C`, `nearBridgeDerivedAddress == MPC_ADDR`.
3. Attacker submits `P` on the **original** chain → `completedTransfers[N]` set to `true`, tokens released to recipient.
4. Attacker submits the same `P` on the **forked** chain → `completedTransfers[N]` is still `false` there; `ECDSA.recover(keccak256(borshEncoded), signatureData)` returns `MPC_ADDR` (identical digest, identical key) → tokens released again.
5. Net result: one NEAR-side lock, two EVM-side releases — unbacked supply on the forked chain.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L42-42)
```text
    uint8 public omniBridgeChainId;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L72-79)
```text
    function initialize(
        address tokenImplementationAddress_,
        address nearBridgeDerivedAddress_,
        uint8 omniBridgeChainId_
    ) public initializer {
        tokenImplementationAddress = tokenImplementationAddress_;
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
        omniBridgeChainId = omniBridgeChainId_;
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-287)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L289-313)
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
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```
