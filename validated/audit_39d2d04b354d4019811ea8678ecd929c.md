Found it. The Kaia Service Chain bridge contract `BridgeTransferKLAY.sol` contains a fallback function that mirrors exactly the Morpheus bug pattern: it silently assumes `msg.sender` is a valid receiving address on the destination chain.

### Title
Fallback KLAY-bridge deposit silently locks funds for smart-contract-wallet senders by assuming `msg.sender` is a valid recipient on the other chain - (File: `contracts/service_chain/bridge/BridgeTransferKLAY.sol`)

### Summary
`BridgeTransferKLAY`'s fallback function (`function () external payable`) automatically routes cross-chain KLAY transfers to `msg.sender`, assuming the caller's address is meaningful and controllable on the destination chain. Any unprivileged user who sends plain KLAY to the bridge contract from a smart-contract wallet (multisig, AA wallet, proxy, etc.) will have their funds directed to that same contract address on the paired chain, where no such contract exists and no one can control it — an exact analog of the reported Morpheus `Distribution.sol`/`L1Sender.sol` bug where the code assumed cross-chain address equality.

### Finding Description
The fallback function is: [1](#0-0) 

```solidity
// () requests transfer KLAY to msg.sender address on relative chain.
function () external payable {
    _requestKLAYTransfer(msg.sender, feeOfKLAY, new bytes(0));
}
```

This delegates to `_requestKLAYTransfer`, which emits a `RequestValueTransfer` event with `_to = msg.sender`: [2](#0-1) 

The paired bridge on the other chain (SubBridge/MainBridge) picks up this event and calls `handleKLAYTransfer`, which unconditionally sends value to `_to` via a low-level call: [3](#0-2) 

```solidity
(bool ok, ) = _to.call.value(_value)("");
require(ok, "handleKLAYTransfer: transfer failed");
```

As documented in `node/sc/doc.go`, this bridging mechanism is a general-purpose, user-triggerable cross-chain value transfer that any EOA or contract can initiate by simply sending KLAY to the bridge address: [4](#0-3) 

The bug: if the sender of the plain-value transfer is a smart contract (multisig wallet, AA wallet, proxy, or any contract without corresponding deployed code/ownership at the same address on the destination chain), the destination-chain `_to.call.value(_value)("")` either:
1. Sends funds to an address with no code (a "dumb" EOA-equivalent address) that nobody controls a private key for, permanently locking the funds, or
2. If a different, unrelated contract happens to exist at that address on the destination chain, sends funds to an unrelated entity, or
3. Reverts if `_to` is a contract without a payable fallback, causing the transfer to fail after the funds were already deducted/locked on the source chain (griefing/loss potential during handling, subject to the bridge's retry/vote mechanism).

Unlike `requestKLAYTransfer(address _to, ...)`, which lets a careful user supply an explicit destination address, the bare fallback path silently uses `msg.sender` without any warning, exactly mirroring the flaw called out in the Morpheus report where `Distribution.sol`/`L1Sender.sol` pass the L1 `user_` address as the L2 mint target without accounting for address-space divergence between chains.

### Impact Explanation
Users (especially those using multisig/contract wallets, which are common for treasuries and institutional users) who send KLAY directly to the bridge contract via a plain value transfer will have their funds routed to an address on the counterpart chain that they may not control. This results in a permanent loss of value transferred, matching the "Medium/High" bar of unauthorized value movement/loss reachable by a single unprivileged transaction from a normal user (not an operator, validator, or node).

### Likelihood Explanation
This requires no special privilege — any account, including smart contract wallets, can trigger it by sending value directly to the bridge address (e.g., accidentally, or via a wallet UI that performs a plain transfer rather than calling `requestKLAYTransfer` explicitly). Given that Service Chain bridges are a core supported feature (`node/sc`) intended for general use, and contract-wallet adoption is common, the likelihood of this occurring is realistic, though it depends on users/integrators using the raw fallback path rather than the explicit `requestKLAYTransfer(_to, ...)` function.

### Recommendation
Remove or restrict the bare fallback path for cross-chain transfers, or explicitly reject calls from contract addresses (`msg.sender.code.length > 0` equivalent, noting Solidity 0.5.6 idioms) unless they also pass an explicit destination address. At minimum, emit a prominent warning/document requirement that only EOAs (or addresses control-verified on both chains) should use the bare-fallback deposit path, directing all other senders to `requestKLAYTransfer` with an explicit `_to`.

### Proof of Concept
1. Deploy/attach to a running Service Chain pair (MainBridge chain A / SubBridge chain B) with `BridgeTransferKLAY` deployed on chain A, unlocked (`isLockedKLAY == false`).
2. From a multisig/AA/proxy contract wallet `W` (which has no corresponding contract or owned key at the same address on chain B), send a plain KLAY transfer (no calldata) to the `BridgeTransferKLAY` contract on chain A.
3. This invokes the fallback `function () external payable` at [1](#0-0) , emitting `RequestValueTransfer(..., _to = W, ...)`.
4. The SubBridge on chain B observes the event and its operators call `handleKLAYTransfer(..., _to = W, ...)` on chain B's bridge contract, executing `_to.call.value(_value)("")` at line 98.
5. Because `W`'s address on chain B is not backed by the same private key/contract logic, the funds are either permanently stranded at an uncontrolled address or the call reverts, depending on what (if anything) occupies that address on chain B — reproducing the "rewards/funds minted/sent to the wrong, uncontrollable address" root cause described in the reference report.

### Citations

**File:** contracts/service_chain/bridge/BridgeTransferKLAY.sol (L61-100)
```text
    // handleKLAYTransfer sends the KLAY by the request.
    function handleKLAYTransfer(
        bytes32 _requestTxHash,
        address _from,
        address payable _to,
        uint256 _value,
        uint64 _requestedNonce,
        uint64 _requestedBlockNumber,
        bytes memory _extraData
    )
        public
        onlyOperators
        nonReentrant
    {
        _lowerHandleNonceCheck(_requestedNonce);

        if (!_voteValueTransfer(_requestedNonce)) {
            return;
        }

        _setHandledRequestTxHash(_requestTxHash);

        handleNoncesToBlockNums[_requestedNonce] = _requestedBlockNumber;
        _updateHandleNonce(_requestedNonce);

        emit HandleValueTransfer(
            _requestTxHash,
            TokenType.KLAY,
            _from,
            _to,
            address(0),
            _value,
            _requestedNonce,
            lowerHandleNonce,
            _extraData
        );

        (bool ok, ) = _to.call.value(_value)("");
        require(ok, "handleKLAYTransfer: transfer failed");
    }
```

**File:** contracts/service_chain/bridge/BridgeTransferKLAY.sol (L102-124)
```text
    // _requestKLAYTransfer requests transfer KLAY to _to on relative chain.
    function _requestKLAYTransfer(address _to, uint256 _feeLimit,  bytes memory _extraData)
        internal
        unlockedKLAY
        nonReentrant
    {
        require(isRunning, "stopped bridge");
        require(msg.value > _feeLimit, "insufficient amount");

        uint256 fee = _payKLAYFeeAndRefundChange(_feeLimit);

        emit RequestValueTransfer(
            TokenType.KLAY,
            msg.sender,
            _to,
            address(0),
            msg.value.sub(_feeLimit),
            requestNonce,
            fee,
            _extraData
        );
        requestNonce++;
    }
```

**File:** contracts/service_chain/bridge/BridgeTransferKLAY.sol (L126-129)
```text
    // () requests transfer KLAY to msg.sender address on relative chain.
    function () external payable {
        _requestKLAYTransfer(msg.sender, feeOfKLAY, new bytes(0));
    }
```

**File:** node/sc/doc.go (L37-47)
```go
Unlike the block data anchoring, user data transfer is bi-directional.
For example, users can transfer KAIA of Kaia main chain to an address of a Service Chain or vice versa.
This kind of inter-chain operation requires read/write ability on both chains but does not use MainBridge functions in the process.
Instead of the MainBridge, the SubBridge in the child chain directly calls read/write operations to the parent chain node through RPC (In the basic configuration, the parent chain node is the same with the MainBridge enabled node).
Of course, the accounts of both chains should be registered on the SubBridge to generate transactions.
Following is the process of the KAIA transfer from Kaia main chain to a Service Chain.
1. A user executes the inter-chain operation by sending a transaction with KAIA to the bridge contract of Kaia main chain.
2. The bridge contract keeps KAIA on its account and creates an event for the inter-chain request.
3. The SubBridge subscribes the event log on the main chain node through RPC.
4. The SubBridge generates a transaction on the child chain node to the bridge contract of the SubBridge.
5. Finally, The bridge contract mints (or uses its KAIA) and sends KAIA to the target address.
```
