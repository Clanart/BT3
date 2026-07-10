### Title
Permanently Locked ETH via Unrecoverable `nativeFee` in `initTransfer` — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The `initTransfer` payable function in `OmniBridge.sol` accepts ETH as `nativeFee` from the caller but has no mechanism to withdraw or distribute that ETH on the EVM side. Any ETH sent as `nativeFee` is irrecoverably locked in the bridge contract.

---

### Finding Description

`initTransfer` is a `payable` function that handles two token paths:

**ERC-20 path** (`tokenAddress != address(0)`):
```solidity
extensionValue = msg.value - nativeFee;
```

**Native ETH path** (`tokenAddress == address(0)`):
```solidity
extensionValue = msg.value - amount - nativeFee;
```

In both paths, `nativeFee` worth of ETH is implicitly retained by the contract — it is subtracted from `msg.value` before computing `extensionValue`, which is the only portion forwarded onward (to Wormhole in `OmniBridgeWormhole`, or checked to be zero in the base `OmniBridge`). [1](#0-0) 

The `nativeFee` ETH is never transferred to any relayer, fee recipient, or treasury on the EVM side. The contract has no `withdrawNativeFee`, `rescueETH`, or equivalent function. The only ETH-disbursing path is `finTransfer` for native-ETH transfers, which sends `payload.amount` to the recipient — it does not touch accumulated `nativeFee` balances. [2](#0-1) 

The contract also exposes a bare `receive()` fallback, meaning any ETH sent directly is similarly unrecoverable. [3](#0-2) 

The NEAR-side `claim_fee` function distributes fees in NEAR tokens or bridged tokens — it has no mechanism to repatriate ETH locked in the EVM contract. [4](#0-3) 

---

### Impact Explanation

Every call to `initTransfer` with `nativeFee > 0` permanently locks that ETH in the EVM bridge contract. There is no admin withdrawal function, no relayer claim function, and no refund path on the EVM side. This constitutes **permanent, irrecoverable lock of user funds in a bridge vault flow**, matching the Critical/High allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

Over time, as the bridge accumulates `nativeFee` ETH from many users, the locked amount grows without bound and is never recoverable.

---

### Likelihood Explanation

The `nativeFee` parameter is a standard, documented user-facing input. Users bridging tokens from EVM to NEAR are expected to set `nativeFee` to compensate relayers (mirroring the NEAR-side `native_token_fee` field). Any user who follows this expectation and sets `nativeFee > 0` will lose that ETH. No special privileges, no race condition, and no complex setup are required — a single ordinary `initTransfer` call is sufficient to trigger the loss. [5](#0-4) 

---

### Recommendation

1. **Add a withdrawal mechanism** with access control (e.g., `DEFAULT_ADMIN_ROLE`) allowing authorized parties to recover accumulated `nativeFee` ETH from the contract.
2. **Alternatively, enforce `nativeFee == 0` on EVM** with an explicit revert if the EVM side is not intended to collect native fees, preventing users from accidentally locking ETH.
3. **Track `nativeFee` per transfer** in a mapping so that the correct relayer can claim exactly the fee associated with their finalized transfer, rather than leaving it pooled and inaccessible.

---

### Proof of Concept

1. User calls `initTransfer` on the EVM bridge with `tokenAddress = USDC`, `amount = 100e6`, `nativeFee = 0.01 ether`, sending `msg.value = 0.01 ether + wormhole.messageFee()`.
2. Inside `initTransfer`: `extensionValue = msg.value - nativeFee = wormhole.messageFee()`. The `0.01 ether` `nativeFee` stays in the contract.
3. In `OmniBridgeWormhole.initTransferExtension`, `extensionValue` (exactly `messageFee()`) is forwarded to Wormhole. The transaction succeeds.
4. The `0.01 ether` is now in the bridge contract with no function to retrieve it.
5. Repeat for N users — the contract accumulates N × `nativeFee` ETH, all permanently locked. [6](#0-5) [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L317-322)
```text
        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L386-413)
```text
        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
            if (customMinters[tokenAddress] != address(0)) {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    customMinters[tokenAddress],
                    amount
                );
                ICustomMinter(customMinters[tokenAddress]).burn(
                    tokenAddress,
                    amount
                );
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```

**File:** near/omni-bridge/src/lib.rs (L1054-1064)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
        self.verify_proof(args.chain_kind, args.prover_args).then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(env::attached_deposit())
                .with_static_gas(CLAIM_FEE_CALLBACK_GAS)
                .claim_fee_callback(&env::predecessor_account_id()),
        )
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L141-148)
```text
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

```
