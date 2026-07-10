### Title
Silent Zero-Padding of Short `H256` Starknet Recipient Addresses Causes Irrecoverable Misdirection of Bridged Funds - (File: `near/omni-types/src/hex_types.rs`)

### Summary

`H256`, the type used for all Starknet addresses in the Omni Bridge (`StarknetAddress = H256`), is instantiated with a `$padded = true` flag that silently left-pads any under-length hex string with leading zeros. `H160` (used for every EVM chain) uses `$padded = false` and strictly rejects short inputs. A user who provides a truncated Starknet recipient address (e.g., one hex character short due to a copy-paste error) will have their tokens locked or burned on NEAR, the MPC will sign the transfer message containing the silently-mutated address, and the Starknet `fin_transfer` will deliver the tokens to the wrong contract address. The funds are permanently unrecoverable.

### Finding Description

In `near/omni-types/src/hex_types.rs`, the shared `impl_h_type!` macro generates `FromStr` for both `H160` and `H256`:

```rust
// near/omni-types/src/hex_types.rs  lines 21-31
fn from_str(s: &str) -> Result<Self, Self::Err> {
    let hex_str = s.strip_prefix("0x").unwrap_or(s);
    if hex_str.len() > $size * 2 {          // only rejects TOO LONG
        return Err(TypesError::InvalidHexLength);
    }
    let hex_str = if $padded {
        &format!("{:0>width$}", hex_str, width = $size * 2)  // silent left-pad
    } else {
        hex_str
    };
    ...
}
```

The two instantiations are:

```rust
// near/omni-types/src/hex_types.rs  lines 103-104
impl_h_type!(H160, 20, false);   // EVM  – strict, rejects short input
impl_h_type!(H256, 32, true);    // Strk – permissive, silently pads short input
```

`StarknetAddress` is a type alias for `H256`:

```rust
// near/omni-types/src/lib.rs  line 172
pub type StarknetAddress = H256;
```

`OmniAddress::Strk` holds a `StarknetAddress`, and `OmniAddress::from_str` parses it via `H256::from_str`:

```rust
// near/omni-types/src/lib.rs  line 407
"strk" => Ok(Self::Strk(recipient.parse().map_err(stringify)?)),
```

The test suite explicitly documents and asserts the padding behaviour:

```rust
// near/omni-types/src/hex_types.rs  lines 172-174
let short = H256::from_str("0x1").unwrap();
assert_eq!(short.0[31], 1);
assert!(short.0[..31].iter().all(|&b| b == 0));
```

A user who intends to send to `strk:0x05558831a603eca8cd69a42d4251f08de3573039b69f23972265cac76639f1cf` but accidentally submits `strk:0x05558831a603eca8cd69a42d4251f08de3573039b69f23972265cac76639f1` (63 hex chars, one char short) will have the address silently rewritten to `strk:0x0005558831a603eca8cd69a42d4251f08de3573039b69f23972265cac76639f1` — a completely different Starknet contract address — without any error or warning.

### Impact Explanation

The NEAR bridge `init_transfer` accepts the silently-mutated `OmniAddress::Strk` as the `recipient` field of `TransferMessage` without any additional length or canonical-form check:

```rust
// near/omni-bridge/src/lib.rs  lines 540-544
let transfer_message = TransferMessage {
    ...
    recipient: init_transfer_msg.recipient,   // already silently mutated
    ...
};
```

The MPC then signs this `TransferMessage` containing the wrong address. The signed payload is submitted to the Starknet `fin_transfer`, which mints or transfers tokens directly to `payload.recipient` — the zero-padded wrong address. The user's tokens are permanently delivered to an address they do not control. There is no refund path once the MPC signature is issued and the Starknet finalization executes.

This matches: **Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation

Starknet addresses are 252-bit felt values commonly displayed as 64 hex characters with a leading `0x`. A single-character truncation (e.g., from a clipboard copy that drops the last character) produces a 63-char hex string that `H256::from_str` silently accepts and pads. This is a realistic user-interface error. The asymmetry with EVM addresses (which are strictly rejected when short) means users and integrators have no reason to expect that Starknet addresses are treated differently. Any direct caller of the NEAR bridge (CLI, SDK, dApp without client-side validation) is exposed.

### Recommendation

Change `H256` to use `$padded = false`, matching the strict behaviour of `H160`:

```rust
impl_h_type!(H160, 20, false);
impl_h_type!(H256, 32, false);   // reject short Starknet addresses
```

Alternatively, introduce a dedicated `StarknetAddress` type with its own `FromStr` that requires exactly 64 hex characters (or exactly 63 for the canonical leading-zero form). Any caller that legitimately needs to express a small felt252 value (e.g., `0x1`) should zero-pad it explicitly before submission.

### Proof of Concept

1. User intends to bridge tokens to Starknet address `0x05558831a603eca8cd69a42d4251f08de3573039b69f23972265cac76639f1cf` (64 hex chars).
2. Due to a copy-paste error, the user submits `recipient = "strk:0x05558831a603eca8cd69a42d4251f08de3573039b69f23972265cac76639f1"` (63 hex chars).
3. `OmniAddress::from_str` calls `H256::from_str("05558831a603eca8cd69a42d4251f08de3573039b69f23972265cac76639f1")`.
4. `hex_str.len()` is 63, which is ≤ 64, so the length guard passes.
5. The `$padded = true` branch formats it as `"0005558831a603eca8cd69a42d4251f08de3573039b69f23972265cac76639f1"` — a completely different 32-byte value.
6. `OmniAddress::Strk(H256([0x00, 0x05, 0x55, ...]))` is stored as the recipient; no error is returned.
7. The NEAR bridge locks/burns the user's tokens and stores the `TransferMessage` with the mutated recipient.
8. MPC signs the message; relayer submits to Starknet `fin_transfer`.
9. Starknet mints/transfers tokens to `0x0005558831a603eca8cd69a42d4251f08de3573039b69f23972265cac76639f1` — an address the user does not control.
10. Funds are permanently lost.

The root cause is exclusively in production file `near/omni-types/src/hex_types.rs` line 104 (`impl_h_type!(H256, 32, true)`), with the vulnerable parse path exercised through `near/omni-types/src/lib.rs` line 407 and the fund-locking entry point at `near/omni-bridge/src/lib.rs` lines 523–553. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** near/omni-types/src/hex_types.rs (L21-31)
```rust
            fn from_str(s: &str) -> Result<Self, Self::Err> {
                let hex_str = s.strip_prefix("0x").unwrap_or(s);
                if hex_str.len() > $size * 2 {
                    return Err(TypesError::InvalidHexLength);
                }

                let hex_str = if $padded {
                    &format!("{:0>width$}", hex_str, width = $size * 2)
                } else {
                    hex_str
                };
```

**File:** near/omni-types/src/hex_types.rs (L102-104)
```rust
// Generate H160 (20 bytes) and H256 (32 bytes) implementations
impl_h_type!(H160, 20, false);
impl_h_type!(H256, 32, true);
```

**File:** near/omni-types/src/lib.rs (L170-193)
```rust
pub type EvmAddress = H160;
pub type UTXOChainAddress = String;
pub type StarknetAddress = H256;

pub const ZERO_ACCOUNT_ID: &str =
    "0000000000000000000000000000000000000000000000000000000000000000";

#[near(serializers=[borsh])]
#[derive(Debug, Clone, Hash, PartialEq, Eq)]
pub enum OmniAddress {
    Eth(EvmAddress),
    Near(AccountId),
    Sol(SolAddress),
    Arb(EvmAddress),
    Base(EvmAddress),
    Bnb(EvmAddress),
    Btc(UTXOChainAddress),
    Zcash(UTXOChainAddress),
    Pol(EvmAddress),
    HyperEvm(EvmAddress),
    Strk(StarknetAddress),
    Abs(EvmAddress),
    Fogo(SolAddress),
}
```

**File:** near/omni-types/src/lib.rs (L405-408)
```rust
            "btc" => Ok(Self::Btc(recipient.to_string())),
            "zcash" => Ok(Self::Zcash(recipient.to_string())),
            "strk" => Ok(Self::Strk(recipient.parse().map_err(stringify)?)),
            "fogo" => Ok(Self::Fogo(recipient.parse().map_err(stringify)?)),
```

**File:** near/omni-bridge/src/lib.rs (L540-553)
```rust
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
```
