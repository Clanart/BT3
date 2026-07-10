### Title
Native ETH Transfer Coupled With `finTransfer` Finalization Enables Permanent Fund Lock - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
In `OmniBridge.sol`, the `finTransfer` function marks the destination nonce as used **before** attempting to send native ETH to the recipient. If the ETH transfer fails (recipient is a contract that rejects ETH), the entire transaction reverts — including the nonce-marking state change. Because there is no on-chain recovery or cancellation path on NEAR for a pending transfer whose EVM finalization permanently fails, the originating user's NEAR tokens are irrecoverably locked.

### Finding Description
`finTransfer` handles native ETH delivery (when `payload.tokenAddress == address(0)`) via a low-level call:

```solidity
completedTransfers[payload.destinationNonce] = true;   // line 287 — reverted on failure
// ... ECDSA verification ...
if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();           // line 322 — reverts entire tx
}
``` [1](#0-0) 

Because the `revert` unwinds the entire transaction, `completedTransfers[payload.destinationNonce]` is never durably set. Every subsequent relay attempt for the same nonce will also revert if the recipient continues to reject ETH. The transfer is stuck in an infinite retry loop with no escape hatch.

On the NEAR side, the originating tokens are locked inside the bridge contract the moment `init_transfer` succeeds. The only legitimate release path is a successful `claim_fee` call, which itself requires proof that `finTransfer` completed on EVM:

```rust
pub fn claim_fee_callback(...) -> PromiseOrValue<()> {
    let Ok(ProverResult::FinTransfer(fin_transfer)) = call_result else {
        env::panic_str(BridgeError::InvalidProofMessage...)
    };
    // ...
    let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);
``` [2](#0-1) 

If `finTransfer` can never succeed, `claim_fee` can never produce a valid proof, and `remove_transfer_message` is never called. The pending transfer entry and the locked tokens remain in the NEAR bridge forever.

There is no cancel-transfer or emergency-refund function in the NEAR bridge contract.

### Impact Explanation
**Critical — Permanent freezing / irrecoverable lock of user funds.**

A user who bridges native ETH to any EVM-side contract address that does not implement a payable `receive()` / `fallback()` (e.g., a multisig, a proxy, a contract upgraded after the transfer was initiated, or a contract whose `receive()` conditionally reverts) will have their NEAR-side tokens permanently locked with no recovery path. The bridge provides no mechanism to cancel a pending transfer or refund the originating chain after EVM finalization fails.

### Likelihood Explanation
**Medium.** Smart-contract recipients that do not accept raw ETH are common (Gnosis Safe, many proxy patterns, ERC-4337 accounts). A user who bridges native ETH to such an address — whether by mistake or because the contract was upgraded between initiation and finalization — triggers the permanent lock. An adversary can also deploy a contract that initially accepts ETH, solicit a victim's bridge transfer to it, then upgrade the contract to reject ETH before the relayer calls `finTransfer`, permanently locking the victim's funds.

### Recommendation
Decouple the ETH delivery from the finalization state change, mirroring the fix described in the original report:

1. Mark `completedTransfers[payload.destinationNonce] = true` and record the pending ETH credit in a separate mapping (pull-payment / withdrawal pattern).
2. Expose a separate `claimEth(uint64 nonce)` function that the recipient calls to pull their ETH.
3. This ensures finalization is never blocked by a recipient's inability to receive ETH, and funds remain claimable without locking the originating chain.

### Proof of Concept

1. Alice holds NEAR tokens and calls `ft_transfer_call` on the NEAR bridge, initiating a transfer of native ETH to `VictimContract` on EVM. `VictimContract` has no `receive()` function (or its `receive()` reverts).
2. The NEAR bridge locks Alice's tokens and stores the pending transfer.
3. A relayer calls `OmniBridge.finTransfer(sig, payload)` where `payload.tokenAddress == address(0)` and `payload.recipient == address(VictimContract)`.
4. Execution reaches line 319: `VictimContract.call{value: payload.amount}("")` returns `success = false`.
5. Line 322 executes `revert FailedToSendEther()`, unwinding the entire transaction including the `completedTransfers[nonce] = true` write.
6. Every subsequent relay attempt for the same nonce repeats steps 3–5 and reverts.
7. Alice's NEAR tokens remain locked in `pending_transfers` indefinitely. `claim_fee` requires a proof of a completed EVM `finTransfer` event, which can never be generated. Alice's funds are permanently frozen. [3](#0-2) [4](#0-3)

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

**File:** near/omni-bridge/src/lib.rs (L1057-1064)
```rust
    pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
        self.verify_proof(args.chain_kind, args.prover_args).then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(env::attached_deposit())
                .with_static_gas(CLAIM_FEE_CALLBACK_GAS)
                .claim_fee_callback(&env::predecessor_account_id()),
        )
    }
```

**File:** near/omni-bridge/src/lib.rs (L1075-1094)
```rust
        let Ok(ProverResult::FinTransfer(fin_transfer)) = call_result else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };

        let fee_recipient = fin_transfer.fee_recipient.unwrap_or_else(|| {
            env::panic_str(BridgeError::FeeRecipientNotSetOrEmpty.to_string().as_str());
        });

        require!(
            fee_recipient == *predecessor_account_id,
            BridgeError::OnlyFeeRecipientCanClaim.as_ref()
        );
        require!(
            self.factories
                .get(&fin_transfer.emitter_address.get_chain())
                == Some(fin_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);
```
