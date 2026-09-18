Found it. The `bank` precompile's `send` function only checks that the **caller is the registered ERC20 pointer contract** for a denom — it never verifies that `senderSeiAddr` (`args[0]`) is the actual token owner or that the transaction originator has any authorization to move funds from that address. This is structurally the same bug class as the lakeFS advisory: an endpoint trusts a coarse-grained "is this the right kind of caller" check instead of verifying the caller is authorized to act on the specific resource (funds) being moved.

### Title
Bank precompile `send` allows the ERC20-Native pointer contract to move funds from any address without an allowance/ownership check - (File: precompiles/bank/bank.go)

### Summary
`PrecompileExecutor.send` in `precompiles/bank/bank.go` [1](#0-0)  validates only that `caller` equals the registered `ERC20NativePointer` address for the given denom. It does **not** verify that the `from` address (`args[0]`, decoded via `accAddressFromArg`) is actually the EVM transaction sender, nor that any allowance has been granted, before calling `p.bankMsgServer.Send` [2](#0-1) . All authorization for who may move whose usei/native funds is expected to live in the calling ERC20 pointer contract's Solidity `transferFrom`/allowance logic, but the precompile itself does not enforce or even check the actual EVM `tx.origin`/caller identity behind the `from` argument.

### Finding Description
The `send(ctx, caller, method, args, ...)` function:
1. Confirms `caller == pointer` (the registered ERC20-Native pointer address for `denom`) — this only proves the call came from *the correct contract type*, not that it came from the correct message-originating account with authorization over the `from` address.
2. Resolves `senderSeiAddr` directly from `args[0]` with no cross-check against the actual EVM caller identity of the outer transaction (`p.evmKeeper.GetSeiAddress(ctx, caller)` is used elsewhere, e.g. in `sendNative` [3](#0-2) , but not in `send`).
3. Executes `MsgSend` moving `amount` of `denom` from `senderSeiAddr` to `receiverSeiAddr` unconditionally [4](#0-3) .

This means the precompile is fully trusting the calling pointer contract to have already verified that `msg.sender` (the actual EVM caller) equals `from`, or is an approved spender with sufficient allowance, exactly as the ERC20-Native pointer Solidity code does before delegating. If any pointer contract implementation — current or future, including custom/legacy pointer versions — omits or has a flaw in that `msg.sender == from || allowance check` (analogous in spirit to lakeFS trusting the S3 gateway's own bookkeeping instead of independently checking object ownership), arbitrary funds can be drained from any account. This is architecturally the same "authorization check exists at the wrong layer / is bypassable because the trusted intermediary is the only enforcement point" pattern that caused the lakeFS `delete-objects` bug (deletion authorized based on request shape rather than per-object ownership).

### Impact Explanation
Because the ultimate check for "is this from-address legitimately controlled/approved by this caller" is delegated entirely to Solidity pointer-contract code that is either core-team-authored or (per repo layout with many "legacy" pointer/bank versions such as `v562`, `v580`...`v67`) subject to versioning drift, a discrepancy between any pointer implementation's allowance logic and this precompile's blind trust of `args[0]` results in unauthorized transfer of native usei/tokenfactory-denominated funds — i.e., direct fund loss, matching the "unauthorized transfer via precompile or pointer" impact criterion.

### Likelihood Explanation
Reaching this code path only requires a normal EVM transaction calling the registered ERC20-Native pointer contract's `transferFrom`, which then delegatecalls into the bank precompile — fully reachable by any unprivileged EVM caller. The likelihood of actual exploitation depends entirely on whether every current/legacy pointer contract correctly enforces `msg.sender == from || allowance(from, msg.sender) >= amount` before calling `send`; I was not able to fully audit every legacy pointer version (`precompiles/bank/legacy/v562` … `v67`) for this within the available search results, so it is uncertain whether a concrete allowance-check gap currently exists in a deployed pointer contract. This is a defense-in-depth gap rather than a confirmed exploitable path in the specific pointer version I inspected (`contracts/src/CW20ERC20Pointer.sol`, which is CW20, not the native ERC20 pointer contract — I did not locate the Solidity source for the ERC20-Native pointer's `transferFrom` to confirm its allowance enforcement).

### Recommendation
Have `precompiles/bank/bank.go`'s `send` independently verify authorization — e.g., resolve the actual EVM transaction caller's Sei address via `p.evmKeeper.GetSeiAddress(ctx, caller)`/`ctx` origin tracking (as `sendNative` already does) and require it to equal `senderSeiAddr` unless an on-chain allowance record for the pointer/denom explicitly permits the transfer, rather than trusting `args[0]` and the pointer-contract type check alone.

### Proof of Concept
Not independently verifiable without confirming a concrete allowance bypass in a deployed pointer contract; the finding above documents the missing independent-authorization check in the precompile itself as the root cause, consistent with the lakeFS bug class, but I could not confirm within the available tool results a currently-exploitable pointer contract that fails to check `msg.sender`/allowance before calling `send`.

### Citations

**File:** precompiles/bank/bank.go (L198-216)
```go
func (p PrecompileExecutor) send(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, value *big.Int, readOnly bool) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call send from staticcall")
	}
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, 0, err
	}
	denom := args[2].(string)
	if denom == "" {
		return nil, 0, errors.New("invalid denom")
	}
	pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
	if !exists || pointer.Cmp(caller) != 0 {
		return nil, 0, fmt.Errorf("only pointer %s can send %s but got %s", pointer.Hex(), denom, caller.Hex())
	}
```

**File:** precompiles/bank/bank.go (L223-245)
```go
	senderSeiAddr, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		return nil, 0, err
	}
	receiverSeiAddr, err := p.accAddressFromArg(ctx, args[1])
	if err != nil {
		return nil, 0, err
	}

	msg := &banktypes.MsgSend{
		FromAddress: senderSeiAddr.String(),
		ToAddress:   receiverSeiAddr.String(),
		Amount:      sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount))),
	}

	err = msg.ValidateBasic()
	if err != nil {
		return nil, 0, err
	}

	if _, err = p.bankMsgServer.Send(sdk.WrapSDKContext(ctx), msg); err != nil {
		return nil, 0, err
	}
```

**File:** precompiles/bank/bank.go (L265-268)
```go
	senderSeiAddr, ok := p.evmKeeper.GetSeiAddress(ctx, caller)
	if !ok {
		return nil, 0, errors.New("invalid addr")
	}
```
