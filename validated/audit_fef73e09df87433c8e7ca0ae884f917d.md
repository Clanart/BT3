### Title
`feeRecipient` Borsh Option Encoding Omitted in Wormhole `FinTransfer` Message, Causing Unclaimable Fees — (`evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

---

### Summary

`OmniBridgeWormhole.finTransferExtension` publishes a Wormhole message encoding `feeRecipient` as a plain Borsh `String`. However, the canonical Borsh encoding used everywhere else in the protocol (including the MPC signature payload in `OmniBridge.finTransfer`) encodes `feeRecipient` as a Borsh `Option<String>` (`\x00` for None, `\x01 + encoded_string` for Some). The NEAR bridge's `claim_fee_callback` treats the parsed field as `Option<AccountId>`, meaning the Wormhole prover must deserialize it as an Option. The missing Option-prefix byte causes the prover to misparse the payload, making `claim_fee` permanently fail for all Wormhole-relayed EVM→NEAR transfers that carry a fee recipient, irrecoverably locking relayer fees inside the NEAR bridge contract.

---

### Finding Description

**Canonical encoding in `OmniBridge.finTransfer` (the MPC signature payload):** [1](#0-0) 

```solidity
bytes(payload.feeRecipient).length == 0   // None or Some(String) in rust
    ? bytes("\x00")
    : bytes.concat(
        bytes("\x01"),
        Borsh.encodeString(payload.feeRecipient)
    ),
```

The comment itself says "None or Some(String) in rust", confirming the protocol intends Borsh `Option<String>` semantics.

**Actual encoding in `OmniBridgeWormhole.finTransferExtension` (the Wormhole message):** [2](#0-1) 

```solidity
bytes memory messagePayload = bytes.concat(
    bytes1(uint8(MessageType.FinTransfer)),
    bytes1(payload.originChain),
    Borsh.encodeUint64(payload.originNonce),
    bytes1(omniBridgeChainId),
    Borsh.encodeAddress(payload.tokenAddress),
    Borsh.encodeUint128(payload.amount),
    Borsh.encodeString(payload.feeRecipient)   // ← plain String, no Option prefix
);
```

`Borsh.encodeString` always emits a 4-byte little-endian length followed by the string bytes: [3](#0-2) 

So the two encodings diverge:

| `feeRecipient` value | MPC payload bytes | Wormhole message bytes |
|---|---|---|
| `""` (no fee) | `\x00` (1 byte) | `\x00\x00\x00\x00` (4 bytes) |
| `"alice.near"` (9 chars) | `\x01\x09\x00\x00\x00alice.near` | `\x09\x00\x00\x00alice.near` |

**NEAR side consumes the Wormhole message as `Option<AccountId>`:** [4](#0-3) 

```rust
let Ok(ProverResult::FinTransfer(fin_transfer)) = call_result else { … };
let fee_recipient = fin_transfer.fee_recipient.unwrap_or_else(|| {
    env::panic_str(BridgeError::FeeRecipientNotSetOrEmpty.to_string().as_str());
});
```

`fin_transfer.fee_recipient` is `Option<AccountId>`. The Wormhole prover must deserialize the raw Wormhole payload into this struct, meaning it reads `feeRecipient` as a Borsh `Option<String>`.

**Byte-level misparsing:**

- *Empty feeRecipient*: Wormhole message emits `\x00\x00\x00\x00`. Prover reads byte 0 = `\x00` → `Option::None` (accidentally correct for the discriminant), but the 3 trailing `\x00` bytes bleed into the next field, corrupting the entire remainder of the message.
- *Non-empty feeRecipient* (e.g., `"alice.near"`, 9 bytes): Wormhole message emits `\x09\x00\x00\x00alice.near`. Prover reads byte 0 = `\x09` (non-zero) → `Option::Some`. It then reads the next 4 bytes `\x00\x00\x00al` as the string length = `0x616c0000` = 1,635,549,184. Attempting to read 1.6 GB of string data causes an immediate panic/abort.

In both cases `claim_fee` reverts, and the fee stored in the NEAR bridge's `pending_transfers` map can never be released.

---

### Impact Explanation

Every relayer that finalizes an EVM→NEAR transfer via `OmniBridgeWormhole` and specifies a `feeRecipient` will have their earned fee permanently locked inside the NEAR bridge contract. The `pending_transfers` entry for the transfer remains, but `claim_fee` will always fail because the Wormhole prover cannot correctly deserialize the `FinTransfer` payload. There is no alternative code path to recover these funds. This matches: **permanent freezing / unclaimable settlement of user or protocol funds in bridge flows (Critical)** and **fee accounting corruption that misdirects value (High)**.

---

### Likelihood Explanation

Every `finTransfer` call on `OmniBridgeWormhole` that carries a non-empty `feeRecipient` triggers this bug. Relayers routinely supply a `feeRecipient` to collect their bridging fee. The bug is deterministic and requires no special attacker action — any ordinary bridge user initiating a transfer with a fee causes the subsequent `claim_fee` to be permanently broken.

---

### Recommendation

In `OmniBridgeWormhole.finTransferExtension`, encode `feeRecipient` with the same Borsh `Option<String>` layout used in `OmniBridge.finTransfer`:

```solidity
bytes(payload.feeRecipient).length == 0
    ? bytes("\x00")
    : bytes.concat(bytes("\x01"), Borsh.encodeString(payload.feeRecipient))
```

This aligns the Wormhole message encoding with the canonical Option encoding expected by the NEAR prover and with the comment already present in `OmniBridge.finTransfer`.

---

### Proof of Concept

1. Alice initiates a NEAR→EVM transfer specifying `feeRecipient = "relayer.near"`.
2. Relayer calls `OmniBridgeWormhole.finTransfer(sig, payload)` where `payload.feeRecipient = "relayer.near"`.
3. `finTransferExtension` publishes Wormhole message with bytes: `…\x0c\x00\x00\x00relayer.near` (plain string, 12 chars).
4. NEAR Wormhole prover deserializes: reads byte `\x0c` (12) as Option discriminant → `Some`; reads next 4 bytes `\x00\x00\x00re` as string length = `0x72650000` = 1,919,025,152; panics trying to read ~1.9 GB.
5. `claim_fee_callback` never executes; the fee stored in `pending_transfers` is permanently locked.
6. Relayer retries indefinitely — every attempt produces the same panic. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-311)
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

**File:** evm/src/common/Borsh.sol (L17-27)
```text
    function encodeString(
        string memory val
    ) internal pure returns (bytes memory) {
        return encodeBytes(bytes(val));
    }

    function encodeBytes(
        bytes memory val
    ) internal pure returns (bytes memory) {
        return bytes.concat(encodeUint32(uint32(val.length)), val);
    }
```

**File:** near/omni-bridge/src/lib.rs (L1057-1086)
```rust
    pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
        self.verify_proof(args.chain_kind, args.prover_args).then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(env::attached_deposit())
                .with_static_gas(CLAIM_FEE_CALLBACK_GAS)
                .claim_fee_callback(&env::predecessor_account_id()),
        )
    }

    #[private]
    #[payable]
    pub fn claim_fee_callback(
        &mut self,
        #[serializer(borsh)] predecessor_account_id: &AccountId,
        #[callback_result]
        #[serializer(borsh)]
        call_result: Result<ProverResult, PromiseError>,
    ) -> PromiseOrValue<()> {
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
```
