### Title
StarkNet `fin_transfer` Silently Drops `message` Field During Bridge Token Minting, Causing Irrecoverable Fund Lock for Message-Dependent Recipients — (`starknet/src/omni_bridge.cairo`)

---

### Summary

The StarkNet `OmniBridge.fin_transfer()` does not pass the `message` field from the verified `TransferMessagePayload` to the bridge token's `mint()` call. The EVM counterpart explicitly supports a `mint(recipient, amount, message)` overload for this purpose. Any cross-chain transfer that carries a non-empty `message` and targets a StarkNet contract recipient that depends on that message to handle the received tokens will have the message silently discarded, leaving the minted tokens permanently unprocessable inside the recipient contract.

---

### Finding Description

**EVM `finTransfer` — message IS forwarded to the token:** [1](#0-0) 

```solidity
} else if (isBridgeToken[payload.tokenAddress]) {
    if (payload.message.length == 0) {
        IBridgeToken(payload.tokenAddress).mint(
            payload.recipient,
            payload.amount
        );
    } else {
        IBridgeToken(payload.tokenAddress).mint(
            payload.recipient,
            payload.amount,
            payload.message   // ← message forwarded
        );
    }
}
```

**StarkNet `fin_transfer` — message is NOT forwarded:** [2](#0-1) 

```cairo
if self.is_bridge_token(payload.token_address) {
    IBridgeTokenDispatcher { contract_address: payload.token_address }
        .mint(payload.recipient, payload.amount.into());
        // ↑ message field from payload is silently dropped
```

The `IBridgeToken` trait defined inside the StarkNet module only exposes `mint(recipient, amount)` with no message parameter: [3](#0-2) 

```cairo
#[starknet::interface]
trait IBridgeToken<TContractState> {
    fn mint(ref self: TContractState, recipient: ContractAddress, amount: u256);
    fn burn(ref self: TContractState, account: ContractAddress, amount: u256);
}
```

The `message` field is part of the MPC-signed `TransferMessagePayload` and is verified by `_verify_borsh_signature` before any token action, and it is emitted in the `FinTransfer` event: [4](#0-3) 

```cairo
self.emit(
    Event::FinTransfer(
        FinTransfer {
            ...
            message: payload.message,   // ← present in event, absent from mint()
        },
    ),
)
```

So the message is cryptographically authenticated by MPC and recorded on-chain, but never acted upon.

---

### Impact Explanation

A user bridging tokens from NEAR or EVM to StarkNet may include a `message` payload to instruct a StarkNet smart-contract recipient to perform a follow-up action upon receiving the minted tokens (e.g., deposit into a liquidity pool, stake, or route to a sub-account). On EVM this works because `IBridgeToken.mint(recipient, amount, message)` is called. On StarkNet the message is dropped unconditionally.

If the recipient is a smart contract that requires the message to correctly handle the incoming tokens — and has no alternative recovery path — the minted tokens are permanently locked inside that contract. The transfer is already marked finalised on StarkNet (`_set_transfer_finalised` is called at line 250), so re-submission is blocked by the replay guard. The user cannot reclaim the tokens on the source chain because the source-side proof has already been consumed. The result is an irrecoverable fund lock matching the **Critical** impact class. [5](#0-4) 

---

### Likelihood Explanation

The `message` field is a first-class, MPC-signed part of the transfer protocol on all chains. The EVM bridge already uses it for DeFi integrations. Any integrator building a StarkNet DeFi adapter that mirrors the EVM pattern (expecting `mint` to forward the message) will trigger this silently. The likelihood is **Medium**: it does not affect plain EOA recipients, but it is a latent trap for every contract-recipient integration on StarkNet.

---

### Recommendation

1. Add a `mint_with_message` (or overloaded `mint`) entry point to the StarkNet `IBridgeToken` interface.
2. In `fin_transfer`, branch on `payload.message.len() > 0` and call the message-aware variant when non-empty, mirroring the EVM logic.
3. Alternatively, always pass the message and let the bridge token implementation ignore it when empty, keeping the interface symmetric across all chains.

---

### Proof of Concept

1. Deploy a StarkNet bridge token whose `mint` implementation reads a message from a side-channel (e.g., a storage slot written by a hypothetical `mint_with_message`). Any DeFi adapter expecting this pattern is representative.
2. From NEAR, call `ft_transfer_call` → `ft_on_transfer` → `init_transfer` with `msg = '{"action":"stake"}'` and `recipient = <StarkNet DeFi contract>`.
3. Relayer submits the MPC-signed `TransferMessagePayload` (which includes the message) to StarkNet `fin_transfer`.
4. `_set_transfer_finalised` marks the nonce used; `_verify_borsh_signature` passes (message is in the signed blob).
5. `IBridgeTokenDispatcher.mint(recipient, amount)` is called — message is dropped.
6. Tokens are minted to the DeFi contract, which has no way to know the intended action; tokens are permanently stuck.
7. Re-submission is blocked: `is_transfer_finalised(nonce)` returns `true`. [6](#0-5) [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L337-355)
```text
        } else if (isBridgeToken[payload.tokenAddress]) {
            if (payload.message.length == 0) {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount
                );
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```

**File:** starknet/src/omni_bridge.cairo (L242-263)
```text
        fn fin_transfer(
            ref self: ContractState, signature: Signature, payload: TransferMessagePayload,
        ) {
            assert(!_is_paused(@self, PAUSE_FIN_TRANSFER), 'ERR_FIN_TRANSFER_PAUSED');

            assert(
                !self.is_transfer_finalised(payload.destination_nonce), 'ERR_NONCE_ALREADY_USED',
            );
            _set_transfer_finalised(ref self, payload.destination_nonce);

            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );

            if self.is_bridge_token(payload.token_address) {
                IBridgeTokenDispatcher { contract_address: payload.token_address }
                    .mint(payload.recipient, payload.amount.into());
            } else {
                let success = IERC20Dispatcher { contract_address: payload.token_address }
                    .transfer(payload.recipient, payload.amount.into());
                assert(success, 'ERR_TRANSFER_FAILED');
            }
```

**File:** starknet/src/omni_bridge.cairo (L265-279)
```text
            self
                .emit(
                    Event::FinTransfer(
                        FinTransfer {
                            origin_chain: payload.origin_chain,
                            origin_nonce: payload.origin_nonce,
                            token_address: payload.token_address,
                            amount: payload.amount,
                            recipient: payload.recipient,
                            fee_recipient: payload.fee_recipient,
                            message: payload.message,
                        },
                    ),
                )
        }
```

**File:** starknet/src/omni_bridge.cairo (L413-417)
```text
    #[starknet::interface]
    trait IBridgeToken<TContractState> {
        fn mint(ref self: TContractState, recipient: ContractAddress, amount: u256);
        fn burn(ref self: TContractState, account: ContractAddress, amount: u256);
    }
```
