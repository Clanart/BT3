### Title
Unvalidated BTC/Zcash Recipient Address Accepted Without Format or Checksum Check, Enabling Permanent Fund Lock - (File: near/omni-types/src/lib.rs)

### Summary
`OmniAddress::from_str` and `OmniAddress::new_from_slice` accept any arbitrary UTF-8 string as a Bitcoin or Zcash address with zero format validation. An unprivileged user who supplies a malformed or garbage BTC/Zcash recipient address will have their source-chain tokens permanently locked: the NEAR bridge stores the transfer, the relayer signs it, and the UTXO connector attempts to broadcast to an invalid on-chain address — with no refund path back to the user.

### Finding Description

In `near/omni-types/src/lib.rs`, the `FromStr` implementation for `OmniAddress` dispatches on the chain prefix. For every structured chain (EVM, Solana, Starknet, NEAR) the recipient string is parsed through a typed parser that enforces byte-length, hex encoding, or base58 checksum. For BTC and Zcash the string is accepted verbatim:

```rust
"btc"   => Ok(Self::Btc(recipient.to_string())),
"zcash" => Ok(Self::Zcash(recipient.to_string())),
``` [1](#0-0) 

The same pattern appears in `new_from_slice`, which is the path used when parsing addresses from on-chain event bytes:

```rust
ChainKind::Btc => Ok(Self::Btc(
    String::from_utf8(address.to_vec())
        .map_err(|e| format!("Invalid BTC address: {e}"))?,
)),
ChainKind::Zcash => Ok(Self::Zcash(
    String::from_utf8(address.to_vec())
        .map_err(|e| format!("Invalid ZCash address: {e}"))?,
)),
``` [2](#0-1) 

No Bech32/Bech32m checksum, no length bound, no character-set check, and no Base58Check verification is performed. The codebase itself acknowledges this in integration-test comments:

> "The bridge doesn't validate Zcash address format — `OmniAddress::Zcash(String)` accepts anything" [3](#0-2) 

This is the direct analog of the Dexter finding: checksum/format validation code is either absent or bypassed, and the raw string is accepted as a valid address.

The transfer flow that leads to permanent lock:

1. User calls `ft_transfer_call` on NEAR (or `initTransfer` on EVM) with `recipient = "btc:garbage"` or `"zcash:garbage"`.
2. `init_transfer` stores the `TransferMessage` in `pending_transfers` and locks/burns the user's tokens.
3. A trusted relayer calls `sign_transfer`, which reads the stored message, normalizes the amount, and requests an MPC signature over a `TransferMessagePayload` that embeds the garbage address as `recipient`.
4. The signed payload is forwarded to `submit_transfer_to_utxo_chain_connector`.
5. The UTXO connector attempts to broadcast a Bitcoin/Zcash transaction to the invalid address. The Bitcoin/Zcash network rejects it.
6. No callback or refund path exists in the NEAR bridge for a failed UTXO broadcast; the `pending_transfers` entry remains, and the user's tokens are irrecoverably locked. [4](#0-3) 

### Impact Explanation

A user who mistypes or copy-pastes a malformed BTC or Zcash address loses their bridged tokens permanently. The source-chain tokens are burned/locked at step 2; the UTXO transaction fails silently at step 5; and there is no on-chain refund trigger. This satisfies the allowed impact: **Permanent freezing / irrecoverable lock of user funds in bridge flows**.

### Likelihood Explanation

BTC Bech32m addresses and Zcash Unified Addresses are long, case-sensitive strings that users frequently mistype or truncate. The EVM `initTransfer` function accepts `recipient` as a raw `string calldata` with no on-chain validation, making it trivially easy to submit a malformed address. Any user bridging to BTC or Zcash is exposed. [5](#0-4) 

### Recommendation

**Short term:**
- Add Bech32/Bech32m format validation (length, HRP, checksum) for `OmniAddress::Btc` in `FromStr` and `new_from_slice`.
- Add Base58Check or Bech32 validation for `OmniAddress::Zcash`.
- Reject the transfer at parse time rather than storing it and discovering the failure at the UTXO layer.

**Long term:**
- Implement a UTXO-connector callback that reports broadcast failure back to the NEAR bridge and triggers a refund to the original sender, so that even if an invalid address slips through, funds are not permanently locked.

### Proof of Concept

```rust
// User submits a transfer with a garbage BTC address
let init_msg = InitTransferMsg {
    recipient: OmniAddress::Btc("not_a_real_btc_address".to_string()),
    fee: U128(0),
    native_token_fee: U128(0),
    msg: None,
    external_id: None,
};
// OmniAddress::from_str("btc:not_a_real_btc_address") succeeds — no validation.
// Tokens are locked. Relayer signs. UTXO connector broadcasts to invalid address.
// Bitcoin network rejects. Funds are permanently locked with no refund path.
``` [6](#0-5) [7](#0-6)

### Citations

**File:** near/omni-types/src/lib.rs (L245-252)
```rust
            ChainKind::Btc => Ok(Self::Btc(
                String::from_utf8(address.to_vec())
                    .map_err(|e| format!("Invalid BTC address: {e}"))?,
            )),
            ChainKind::Zcash => Ok(Self::Zcash(
                String::from_utf8(address.to_vec())
                    .map_err(|e| format!("Invalid ZCash address: {e}"))?,
            )),
```

**File:** near/omni-types/src/lib.rs (L389-411)
```rust
impl FromStr for OmniAddress {
    type Err = String;

    fn from_str(input: &str) -> Result<Self, Self::Err> {
        let (chain, recipient) = input.split_once(':').unwrap_or(("eth", input));

        match chain {
            "eth" => Ok(Self::Eth(recipient.parse().map_err(stringify)?)),
            "near" => Ok(Self::Near(recipient.parse().map_err(stringify)?)),
            "sol" => Ok(Self::Sol(recipient.parse().map_err(stringify)?)),
            "arb" => Ok(Self::Arb(recipient.parse().map_err(stringify)?)),
            "base" => Ok(Self::Base(recipient.parse().map_err(stringify)?)),
            "bnb" => Ok(Self::Bnb(recipient.parse().map_err(stringify)?)),
            "pol" => Ok(Self::Pol(recipient.parse().map_err(stringify)?)),
            "hlevm" => Ok(Self::HyperEvm(recipient.parse().map_err(stringify)?)),
            "abs" => Ok(Self::Abs(recipient.parse().map_err(stringify)?)),
            "btc" => Ok(Self::Btc(recipient.to_string())),
            "zcash" => Ok(Self::Zcash(recipient.to_string())),
            "strk" => Ok(Self::Strk(recipient.parse().map_err(stringify)?)),
            "fogo" => Ok(Self::Fogo(recipient.parse().map_err(stringify)?)),
            _ => Err(format!("Chain {chain} is not supported")),
        }
    }
```

**File:** near/omni-tests/src/zcash_stale_transfer_poc.rs (L196-198)
```rust
        // A 500-char "Zcash UA" string. The bridge doesn't validate Zcash
        // address format — `OmniAddress::Zcash(String)` accepts anything —
        // so we use a string of sufficient length to push the actual encoded
```

**File:** near/omni-bridge/src/lib.rs (L491-500)
```rust
        let transfer_payload = TransferMessagePayload {
            prefix: PayloadType::TransferMessage,
            destination_nonce: transfer_message.destination_nonce,
            transfer_id,
            token_address,
            amount: U128(amount_to_transfer),
            recipient: transfer_message.recipient,
            fee_recipient,
            message,
        };
```

**File:** near/omni-bridge/src/lib.rs (L523-557)
```rust
    fn init_transfer(
        &mut self,
        sender_id: AccountId,
        signer_id: AccountId,
        token_id: AccountId,
        amount: U128,
        init_transfer_msg: InitTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );

        self.current_origin_nonce += 1;
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer_msg.get_destination_chain());

        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-380)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
```
