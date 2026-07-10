### Title
Unbounded Gas Forwarding in `finTransfer` Native ETH Delivery Enables Permanent Irrecoverable Lock of User Funds — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The `finTransfer` function in `OmniBridge.sol` delivers native ETH to the recipient using a bare `.call{value: payload.amount}("")` with **no gas stipend**. A recipient contract whose `receive()` function consumes all forwarded gas (e.g., via `gasleft()` spin or unconditional `assert(false)`) will cause every finalization attempt to revert. Because there is no source-chain refund path for a permanently unfinalizeable transfer, the user's NEAR-side tokens are irrecoverably locked.

---

### Finding Description

In `OmniBridge.sol`, the `finTransfer` function handles native-ETH delivery at line 319:

```solidity
if (payload.tokenAddress == address(0)) {
    // slither-disable-next-line arbitrary-send-eth
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();
}
``` [1](#0-0) 

The call carries **no `gas:` field**, so the EVM forwards all remaining gas (minus the 1/64 retained by EIP-150) to `payload.recipient`. A contract at that address can consume the entire forwarded budget — for example with `while(true){}` or `assert(false)` — causing the outer transaction to run out of gas and revert.

Because the revert unwinds the entire call frame, the `completedTransfers[payload.destinationNonce] = true` write at line 287 is also rolled back:

```solidity
completedTransfers[payload.destinationNonce] = true;
``` [2](#0-1) 

The nonce is therefore never consumed, and the relayer can retry — but every retry against a gas-draining recipient will fail identically, regardless of how much gas is supplied. The transfer is permanently unfinalizeable on the EVM side.

On the NEAR side, the user's tokens were locked or burned at `initTransfer` time. There is no source-chain callback or refund path that triggers when EVM finalization permanently fails; the NEAR contract has no visibility into EVM revert outcomes. The user's funds are therefore irrecoverably locked.

---

### Impact Explanation

**Critical — Permanent irrecoverable lock of user funds in the bridge flow.**

A user who bridges native ETH to a contract recipient (their own smart-contract wallet, a multisig, or a deliberately crafted contract) whose `receive()` exhausts gas will find:

- Every `finTransfer` call reverts.
- The destination nonce is never consumed, so no alternative finalization path exists.
- The source-chain tokens (NEAR-side) are permanently locked with no refund mechanism.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

The `payload.recipient` address is chosen by the user who calls `initTransfer` on the source chain. Any user who:

1. Intentionally routes to a gas-draining contract, or
2. Legitimately targets a smart-contract wallet whose `receive()` performs non-trivial work (e.g., emitting events, updating state, calling sub-contracts)

will trigger this condition. The entry path is fully unprivileged and requires no special role. The `finTransfer` function is also callable by any relayer, so the attack surface is wide.

---

### Recommendation

Add an explicit gas stipend to the ETH delivery call, consistent with the fix described in the external report:

```solidity
(bool success, ) = payload.recipient.call{value: payload.amount, gas: 2300}("");
if (!success) revert FailedToSendEther();
```

2 300 gas is sufficient for a plain EOA receive or a simple event-emitting `receive()`, but prevents a recipient contract from consuming the entire transaction budget. If richer recipient logic must be supported, consider a pull-payment pattern (store the owed ETH and let the recipient withdraw it separately), which eliminates the DoS vector entirely.

---

### Proof of Concept

**Attack contract (deployed on EVM):**

```solidity
contract GasDrainRecipient {
    receive() external payable {
        // Consume all forwarded gas unconditionally
        assert(false);
        // or: while (true) {}
    }
}
```

**Attack steps:**

1. Attacker deploys `GasDrainRecipient` on the EVM chain.
2. Attacker calls `initTransfer` on the NEAR bridge, specifying `GasDrainRecipient`'s address as the EVM recipient and `tokenAddress = address(0)` (native ETH transfer).
3. NEAR-side tokens are locked/burned; MPC signers produce a valid signature over the `TransferMessagePayload`.
4. Relayer calls `finTransfer(signatureData, payload)` with `msg.value = payload.amount`.
5. Execution reaches line 319; the `.call{value: payload.amount}("")` forwards ~all remaining gas to `GasDrainRecipient.receive()`.
6. `assert(false)` consumes all gas → outer transaction reverts → `completedTransfers` write is rolled back.
7. Relayer retries with higher gas; step 6 repeats identically.
8. The transfer is permanently unfinalizeable; the user's NEAR tokens are irrecoverably locked. [3](#0-2)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-322)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

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

        MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
```
