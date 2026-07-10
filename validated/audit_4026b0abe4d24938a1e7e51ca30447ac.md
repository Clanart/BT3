### Title
Fee-on-Transfer Token Accounting Mismatch in `initTransfer` Emits Inflated Amount, Causing Bridge Undercollateralization — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

`OmniBridge.sol::initTransfer` records the caller-supplied `amount` in the `InitTransfer` event rather than the actual amount the bridge contract received. For fee-on-transfer ERC20 tokens the two values differ. Because the NEAR side relies exclusively on the emitted event to determine how many tokens to mint, it mints more than were locked, permanently breaking bridge collateralization.

---

### Finding Description

In `initTransfer`, when the token is neither a bridge token nor a custom-minter token, the bridge locks it with a plain `safeTransferFrom`:

```solidity
// OmniBridge.sol lines 407-411
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount          // ← user-supplied value
    );
}
```

Immediately after, the same user-supplied `amount` is written into the event:

```solidity
// OmniBridge.sol lines 427-436
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,             // ← same user-supplied value, not actual received
    fee,
    nativeFee,
    recipient,
    message
);
```

For a fee-on-transfer ERC20 (one whose `transfer`/`transferFrom` silently deducts a fee before crediting the recipient), `address(this)` receives `amount − fee_on_transfer`, yet the event records `amount`. The NEAR side's `fin_transfer_callback` decodes the proof of this event and constructs the `TransferMessage` using `init_transfer.amount` verbatim:

```rust
// near/omni-bridge/src/lib.rs lines 722-727
let transfer_message = TransferMessage {
    origin_nonce: init_transfer.origin_nonce,
    token: init_transfer.token,
    amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
    ...
};
```

The NEAR bridge then mints or releases `amount` tokens to the recipient, not `amount − fee_on_transfer`. The CLAUDE.md invariant confirms the NEAR side has no independent source of truth: *"The NEAR side relies solely on these events — any missing or ambiguous field means lost funds or spoofable transfers."*

---

### Impact Explanation

Every bridging of a fee-on-transfer token creates an unbacked surplus of bridged tokens on NEAR equal to the fee amount. Over time (or in a single large transfer) the EVM vault becomes undercollateralized: the bridge holds fewer tokens than the outstanding NEAR-side supply. When any user attempts to bridge back, the vault cannot cover the full redemption, permanently freezing the shortfall. This matches the allowed impact: **"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value."**

---

### Likelihood Explanation

The bridge explicitly supports arbitrary ERC20 tokens via the non-bridge, non-custom-minter code path. Fee-on-transfer tokens are a well-established token class (e.g., tokens with protocol fees, reflection tokens). Any unprivileged user can trigger this by calling `initTransfer` with such a token. No special role, leaked key, or oracle manipulation is required.

---

### Recommendation

Measure the actual balance change rather than trusting the caller-supplied `amount`:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
// use actualReceived (cast to uint128) in the event and downstream logic
```

Emit `actualReceived` in `InitTransfer` so the NEAR side mints only what was truly locked.

---

### Proof of Concept

1. Deploy a fee-on-transfer ERC20 with a 1 % transfer fee; register it with the bridge via `logMetadata`.
2. Call `initTransfer(tokenAddress, 1000, 0, 0, "alice.near", "")`.
3. `safeTransferFrom` deducts 1 % → bridge receives **990** tokens.
4. `InitTransfer` event is emitted with `amount = 1000`.
5. Relayer submits proof to NEAR; `fin_transfer_callback` reads `amount = 1000` and mints **1000** bridged tokens to `alice.near`.
6. Bridge vault holds 990 tokens; NEAR supply is 1000 → **10-token shortfall per transfer**.
7. Repeat or use a large amount; eventually the vault cannot cover redemptions, permanently freezing user funds.

---

**Root cause location:** [1](#0-0) 

**Event emission with unchecked amount:** [2](#0-1) 

**NEAR side consuming the event amount verbatim:** [3](#0-2) 

**CLAUDE.md invariant confirming NEAR relies solely on event data:** [4](#0-3)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L406-412)
```text
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L427-436)
```text
        emit BridgeTypes.InitTransfer(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
```

**File:** near/omni-bridge/src/lib.rs (L722-727)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
```

**File:** evm/CLAUDE.md (L22-23)
```markdown

**EVM → NEAR (initTransfer)**: User calls `initTransfer` which burns/locks tokens on EVM and emits `InitTransfer` with all transfer details (sender, token, amount, fee, nativeFee, recipient, message). In the Wormhole variant, a Wormhole message is also sent. The NEAR side reads this event (via light client or Wormhole) to complete the transfer. Every field needed to reconstruct the transfer must be in the event — it is the only data the NEAR side sees.
```
