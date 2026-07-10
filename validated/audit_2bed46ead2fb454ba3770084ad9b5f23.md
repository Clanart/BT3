### Title
Permanent Freezing of Native ETH Transfers When Recipient Contract Cannot Accept ETH — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

In `OmniBridge::finTransfer`, when finalizing a NEAR→EVM bridge transfer of native ETH (`tokenAddress == address(0)`), the contract sends ETH directly to `payload.recipient` via a low-level `call`. If the recipient is a smart contract without a `receive()` or `payable fallback()` function, the call fails, the transaction reverts, and the ETH locked in OmniBridge becomes permanently irrecoverable. No fallback delivery, recipient override, or refund path exists.

---

### Finding Description

In `OmniBridge::finTransfer`, the nonce is marked used and then ETH is pushed to the recipient:

```solidity
completedTransfers[payload.destinationNonce] = true;   // line 287

// ...

if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();           // line 319-322
}
``` [1](#0-0) 

When `payload.recipient` is a contract without a `receive()` or `payable fallback()`, `success` is `false`. The function reverts with `FailedToSendEther()`, rolling back the entire transaction including the `completedTransfers` flag. The nonce is therefore not consumed, but every subsequent retry attempt will fail identically.

The OmniBridge contract holds ETH locked from prior EVM→NEAR `initTransfer` calls (users send `amount + nativeFee` ETH, which accumulates in the contract):

```solidity
extensionValue = msg.value - amount - nativeFee;   // line 391
``` [2](#0-1) 

That locked ETH is the source for `finTransfer` payouts. When delivery permanently fails, the corresponding ETH is frozen in the contract. There is no mechanism to:
- Redirect the ETH to a different recipient address
- Wrap it as WETH and deliver as ERC20
- Refund the NEAR-side sender
- Allow the recipient to pull-claim the ETH

The contract does have a `receive() external payable {}` function, confirming it can hold ETH, but no rescue or recovery path for stuck native transfers exists. [3](#0-2) 

In `OmniBridgeWormhole`, `finTransferExtension` additionally forwards `msg.value` to the Wormhole endpoint after the ETH push to the recipient, meaning the relayer's Wormhole fee is also wasted on every failed retry:

```solidity
_wormhole.publishMessage{value: msg.value}(   // line 109
    wormholeNonce,
    messagePayload,
    _consistencyLevel
);
``` [4](#0-3) 

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds.**

The user on NEAR burns or locks their wrapped ETH to initiate the cross-chain transfer. On EVM, the corresponding ETH locked in OmniBridge can never be released to the intended recipient. The NEAR-side state is already finalized (tokens burned/locked), so the user cannot recover their assets on either chain. The ETH remains frozen in OmniBridge indefinitely, with no on-chain recovery path short of a privileged contract upgrade.

---

### Likelihood Explanation

**Medium.** The trigger condition — a contract recipient without `receive()`/`payable fallback()` — is common in practice:

- Gnosis Safe multisigs (widely used by DAOs and protocols) do not accept raw ETH by default unless explicitly configured
- Protocol treasury contracts, vesting contracts, and governance contracts frequently lack ETH receive capability
- Smart contract wallets without ETH receive hooks

A user bridging native ETH from NEAR to an EVM contract address (e.g., a DAO treasury or multisig) can trigger this unintentionally. No special privilege is required; any bridge user specifying a contract as recipient is a potential victim.

---

### Recommendation

Replace the push pattern with a pull pattern for native ETH delivery:

1. **Pull-claim**: If the `call` fails, store `(recipient → amount)` in a claimable mapping instead of reverting. Allow the recipient to call a `claimNativeETH()` function later.
2. **WETH fallback**: On failed ETH push, wrap the amount as WETH and deliver via `safeTransfer` to the recipient.
3. **Pre-flight check**: Before marking the transfer complete, verify the recipient can accept ETH (though this is not fully reliable for all contract types).

The pull-claim approach is the most robust and mirrors the pattern used in other bridge protocols to avoid permanent freezing.

---

### Proof of Concept

1. User on NEAR initiates a transfer of native ETH to EVM, specifying a Gnosis Safe multisig (or any contract without `receive()`) as `recipient`.
2. NEAR-side tokens are burned/locked; the transfer message is signed by MPC.
3. Relayer calls `OmniBridge::finTransfer` (or `OmniBridgeWormhole::finTransfer`) with:
   - `payload.tokenAddress = address(0)`
   - `payload.recipient = <contract_without_receive>`
   - `payload.amount = X`
4. `completedTransfers[nonce] = true` is set transiently.
5. `payload.recipient.call{value: X}("")` returns `(false, "")`.
6. `revert FailedToSendEther()` rolls back the entire transaction.
7. The nonce is not consumed; the relayer retries — and fails again, indefinitely.
8. `X` ETH remains locked in OmniBridge. The NEAR-side user's tokens are permanently lost. No on-chain recovery path exists. [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-322)
```text
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L387-393)
```text
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L96-116)
```text
    function finTransferExtension(
        BridgeTypes.TransferMessagePayload memory payload
    ) internal override {
        bytes memory messagePayload = bytes.concat(
            bytes1(uint8(MessageType.FinTransfer)),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            Borsh.encodeString(payload.feeRecipient)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
            messagePayload,
            _consistencyLevel
        );

        wormholeNonce++;
    }
```
