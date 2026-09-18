## Finding

### Title
Unbounded gas forwarded to EIP‑7702‑delegated EOA in `onERC1155Received`/`onERC1155BatchReceived` callback of the CW1155↔ERC1155 pointer - (File: `contracts/src/CW1155ERC1155Pointer.sol`)

### Summary
`CW1155ERC1155Pointer` reproduces the exact anti-pattern described in the external report: `safeTransferFrom` and `safeBatchTransferFrom` gate the ERC-1155 receiver-acceptance callback purely on `to.code.length > 0` and then invoke `IERC1155Receiver(to).onERC1155Received`/`onERC1155BatchReceived` with no explicit gas stipend, so the callee gets essentially all remaining gas. [1](#0-0) [2](#0-1)  Sei's EVM now supports EIP-7702 SetCode transactions (delegation designators), confirmed by the sender-EOA/delegation check in the giga validation path and the authorization-recovery/pre-association helpers, so an EOA that previously always had `code.length == 0` can install a `0xef0100`-prefixed delegation designator and make `to.code.length > 0` evaluate `true`. [3](#0-2) [4](#0-3) 

### Finding Description
Before EIP-7702, `to.code.length > 0` in the pointer's transfer functions could only be true for genuine smart contracts, so the receiver-acceptance callback and its unbounded gas forwarding were bounded to contracts that opted in by deploying code. With EIP-7702 live on Sei (`ethtypes.ParseDelegation`/`AddressToDelegation` handling and the associated `GetCode`/`GetCodeSize` invariant are already wired into the EVM keeper), any EOA can self-authorize a delegation designator pointing at attacker-controlled implementation code. [5](#0-4)  After that, the same EOA address reports non-zero code size to any caller, including the pointer contract.

`CW1155ERC1155Pointer.safeTransferFrom` first performs the CW1155 `send` via the wasmd precompile (`_execute`) and only afterwards checks `to.code.length`; if non-zero it calls `onERC1155Received` and reverts the whole transaction ("unsafe transfer") unless the correct selector is returned. [1](#0-0)  `safeBatchTransferFrom` has the identical pattern for `onERC1155BatchReceived`. [2](#0-1)  Neither call is wrapped in a gas-capped low-level call; Solidity's `try/external-call` semantics forward all-but-1/64th of the remaining gas (EIP-150), so a `to` address with a 7702 delegation whose implementation performs expensive storage writes in `onERC1155Received` can consume nearly all gas the caller supplied to the transaction before returning the correct selector (or intentionally reverting later). This is reachable by any account that is `msg.sender == from` or an approved operator (`isApprovedForAll`) for a CW1155 balance — i.e., any unprivileged EVM transaction sender, or any third-party contract (marketplace, custodian, batch/airdrop distributor, aggregator) that has been approved to move CW1155-pointer balances on behalf of others.

### Impact Explanation
Any contract or relayer that transfers CW1155-backed ERC1155 pointer tokens to third-party addresses on behalf of multiple users in a single transaction (e.g., an approved marketplace/custodian settling several trades, or a distributor looping `safeTransferFrom`/`safeBatchTransferFrom` calls) can have its gas budget siphoned or its entire transaction forced to revert out-of-gas by placing an EIP-7702-delegated attacker address among the recipients — mirroring the "siphon gas" and "grief the batch / mis-attribute the OOG revert" attacks described in the report. This is a fee/gas-griefing vector against any Sei EVM integrator built on top of the pointer contract, not merely a theoretical concern, since the underlying gas-forwarding defect is unconditional and the `to.code.length` gate that used to make it EOA-safe no longer holds once EIP-7702 is enabled.

### Likelihood Explanation
Likelihood is high for any integrator that batches pointer transfers on behalf of multiple parties: setting a 7702 delegation designator is a normal, permissionless EVM operation available to any EOA, and the pointer contract is a standard, publicly deployed bridging primitive for CW1155↔ERC1155 interop that downstream contracts are expected to call.

### Recommendation
Wrap the `onERC1155Received`/`onERC1155BatchReceived` calls in `contracts/src/CW1155ERC1155Pointer.sol` (and any equivalent CW721/ERC721 pointer using the same `to.code.length` gate) with an explicit gas cap via a low-level `call{gas: CAP}` (mirroring the OpenZeppelin/EIP-1155 fix referenced in the report), so no single callback recipient can consume more than a bounded, small fraction of the caller's supplied gas, and so failures can be attributed precisely to the offending recipient rather than downstream unrelated calls.

### Proof of Concept
1. Attacker EOA signs an EIP-7702 authorization delegating to an attacker-controlled implementation contract whose `onERC1155Received` performs expensive storage writes (e.g., many `SSTORE`s) before returning `0xf23a6e61`, and broadcasts a type-4 SetCode tx installing the delegation designator (per the mechanics validated in `RecoverAddressesFromAuthorization`/`AuthorityToPreAssociate`). [4](#0-3) 
2. A victim contract (marketplace/custodian/distributor) that is `isApprovedForAll` for many CW1155 holders calls `CW1155ERC1155Pointer.safeTransferFrom`/`safeBatchTransferFrom` targeting the attacker's now-delegated address as part of a larger batched settlement.
3. `to.code.length > 0` now evaluates true for the attacker's EOA; the pointer contract invokes `onERC1155Received` with (63/64)² ≈ 96.9% of remaining gas. [6](#0-5) 
4. The attacker's callback burns gas until insufficient gas remains for subsequent operations in the victim's transaction, causing an out-of-gas revert of the whole transaction (or leaving the attacker with subsidized gas for its own storage writes if it returns the selector without exhausting gas).

### Citations

**File:** contracts/src/CW1155ERC1155Pointer.sol (L63-76)
```text
        _execute(bytes(req));
        if (to.code.length > 0) {
            require(
                IERC1155Receiver(to).onERC1155Received(
                    msg.sender,
                    from,
                    id,
                    amount,
                    data
                ) == IERC1155Receiver.onERC1155Received.selector,
                "unsafe transfer"
            );
        }
    }
```

**File:** contracts/src/CW1155ERC1155Pointer.sol (L116-129)
```text
        _execute(bytes(payload));
        if (to.code.length > 0) {
            require(
                IERC1155Receiver(to).onERC1155BatchReceived(
                    msg.sender,
                    from,
                    ids,
                    amounts,
                    data
                ) == IERC1155Receiver.onERC1155BatchReceived.selector,
                "unsafe transfer"
            );
        }
    }
```

**File:** app/app.go (L2855-2868)
```go
	// Sender must be EOA unless delegated (EIP-7702)
	senderCode := app.GigaEvmKeeper.GetCode(ctx, sender)
	if len(senderCode) > 0 {
		_, isDelegated := ethtypes.ParseDelegation(senderCode)
		if !isDelegated {
			return gigaValidationResult{
				err: &abci.ExecTxResult{
					Code: sdkerrors.ErrWrongSequence.ABCICode(),
					Log:  fmt.Sprintf("sender not an eoa: address %s, len(code): %d", sender.Hex(), len(senderCode)),
				},
				baseFee: baseFee,
			}
		}
	}
```

**File:** utils/helpers/address.go (L116-132)
```go
// RecoverAddressesFromAuthorization recovers the EVM address, Sei address, and public
// key of the account that signed an EIP-7702 SetCode authorization (the "authority").
// The authorization sig hash is keccak256(0x05 || rlp([chainId, address, nonce])) and
// the recovery id is carried directly in auth.V (yParity, 0 or 1), which GetAddresses
// expects bumped by 27. This mirrors go-ethereum's SetCodeAuthorization.Authority(), but
// additionally returns the recovered public key so the authority can be associated with
// its true Sei address.
func RecoverAddressesFromAuthorization(auth ethtypes.SetCodeAuthorization) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	var buf bytes.Buffer
	buf.WriteByte(eip7702MagicPrefix)
	if err := rlp.Encode(&buf, []any{auth.ChainID, auth.Address, auth.Nonce}); err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}
	sigHash := crypto.Keccak256Hash(buf.Bytes())
	v := new(big.Int).SetUint64(uint64(auth.V) + 27)
	return GetAddresses(v, auth.R.ToBig(), auth.S.ToBig(), sigHash)
}
```

**File:** x/evm/keeper/code_size_invariant_test.go (L45-53)
```go
	t.Run("eip7702 designator length", func(t *testing.T) {
		designator := ethtypes.AddressToDelegation(common.BytesToAddress([]byte("delegate-target")))
		require.Equal(t, 23, len(designator))
		_, ok := ethtypes.ParseDelegation(designator)
		require.True(t, ok)
		k.SetCode(ctx, addr, designator)
		assertInvariant(t)
		require.Equal(t, 23, k.GetCodeSize(ctx, addr))
	})
```
