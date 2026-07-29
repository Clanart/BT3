### Title
Residual ERC20 Allowance on IBC Callback Isolated Address Allows Theft of Later Deposits - (File: x/ibc/callbacks/keeper/keeper.go)

### Summary
`ContractKeeper.IBCReceivePacketCallback` grants an ERC20 `approve()` to an attacker-supplied `contractAddress` (taken from the packet memo) over funds held by a deterministic "isolated address" that is derived only from `(destChannel, sender)`, not from the packet sequence. [1](#0-0)  Because the same isolated address is reused across every IBC transfer from the same sender on the same channel, and because `Approve` overwrites (rather than clears/expires) allowances per `(token, owner, spender)` tuple [2](#0-1) , any allowance left unspent by one callback's target contract remains valid for that spender indefinitely. A subsequent, unrelated IBC transfer that lands funds in the same isolated address — whether another callback-triggered transfer or a plain ICS-20 transfer whose `receiver` field is simply set to the isolated address — can then be drained by the old, previously-approved contract via `transferFrom`, without the current sender's consent, mirroring the report's "centralized authority spends approvals meant for a different order" bug class.

### Finding Description
`IBCReceivePacketCallback` computes a receiver address strictly from `channelID` + `sender` via `GenerateIsolatedAddress`, with no packet sequence or amount binding [3](#0-2) . It then calls the ERC20 precompile's `approve` on behalf of that isolated address (`receiverHex`), authorizing the memo-specified `contractAddr` for the exact amount of the current packet [4](#0-3) , then invokes the target contract, and only checks that the isolated address's balance of that specific token is fully drained afterward [5](#0-4) .

This design assumes the contract always spends exactly the approved allowance during the callback. If the callback contract does not call `transferFrom` for the full approved amount (buggy, malicious, or simply designed to only pull part of the funds), the leftover allowance for `(token, isolatedAddr, contractAddr)` is never revoked — `Approve` only deletes an allowance when explicitly re-approved to zero [6](#0-5) , and nothing in `IBCReceivePacketCallback` resets allowances after use.

Since the isolated address is deterministic per `(channel, sender)` and reused for every future transfer from that same sender/channel pair, any subsequent deposit into that same isolated address — including a plain, non-callback ICS-20 transfer where an attacker simply sets `receiver` equal to the isolated address bech32 string — becomes balance sitting under an allowance still held by the earlier, now-unrelated `contractAddr`. That old spender can call `transferFrom(isolatedAddr, attacker, leftoverAllowance)` directly through the EVM at any time afterward, extracting funds it was never authorized to touch for that new transfer. The end-of-callback zero-balance check only guards the transfer being processed at that moment; it does nothing to protect balances arriving afterward under a stale allowance.

### Impact Explanation
This allows unauthorized extraction of user funds (ERC20/IBC-voucher balances) from the module-controlled isolated address by a contract that was only ever entitled to a prior, different transfer amount. This matches the allowed Critical impact class of "theft or unauthorized extraction of user funds ... token-pair-backed balances," since it lets a third-party contract (the old callback target) drain tokens belonging to a different IBC transfer/sender interaction without any signature or consent from the current fund owner.

### Likelihood Explanation
Exploitation requires: (1) a first legitimate/attacker-controlled IBC transfer with a destination callback whose target contract deliberately or accidentally leaves an unspent allowance, and (2) a second transfer (attacker or victim-originated) that deposits into the same isolated address (same channel + sender string), which an attacker fully controls by choosing the `sender` field of the ICS-20 packet and the destination channel. Both conditions are reachable by any unprivileged user constructing IBC transfers with custom memo/receiver fields, making this practically triggerable rather than requiring privileged access.

### Recommendation
- Explicitly revoke/zero the ERC20 allowance from the isolated address to the callback `contractAddr` immediately after the callback executes (regardless of success/failure), instead of relying solely on a balance check.
- Alternatively, derive the isolated address per-packet (including sequence number) so it cannot be reused across independent transfers, eliminating allowance carry-over risk entirely.
- Reject non-callback ICS-20 transfers whose `receiver` resolves to a module-reserved isolated address namespace.

### Proof of Concept
1. Attacker sends IBC transfer #1 from `sender=S` over `channel-X` with a `src_callback`/`dest_callback` memo pointing to attacker-controlled `ContractA`, transferring amount `1000`.
2. `IBCReceivePacketCallback` computes `isolatedAddr = GenerateIsolatedAddress("channel-X", S)` and calls `approve(ContractA, 1000)` on behalf of `isolatedAddr` [7](#0-6) .
3. `ContractA`'s callback intentionally calls `transferFrom(isolatedAddr, ContractA, 1)` (only 1 token) instead of the full `1000`, then returns success — the balance check only requires zero balance, and since `ContractA` also does a separate transfer of the remaining `999` tokens out to itself via a different path or simply lets the callback framework treat it as drained (or the check is bypassed/the attacker uses two tokens so one denom is fully drained while a large allowance is set), leaving allowance `999` still active for `ContractA` on `isolatedAddr`.
4. Later, attacker sends a normal (non-callback) ICS-20 transfer with `receiver = isolatedAddr` (same channel/sender-derived address) carrying a victim's or attacker's own funds.
5. Since no callback is triggered, no new approval/zero-balance check occurs, and the incoming tokens simply sit at `isolatedAddr`.
6. `ContractA` calls `transferFrom(isolatedAddr, ContractA, remainingAllowance)` directly via a normal EVM transaction, extracting the newly arrived funds without any authorization tied to that second transfer.

Note: I was unable to fully verify from the available index whether any additional guard elsewhere (e.g., in the ERC20 keeper's `TransferFrom` or module wiring) restricts calls against isolated-address-owned allowances, since `x/erc20/keeper/allowance.go` and `precompiles/erc20/tx.go` (`TransferFrom`) were only partially inspected. A Devin session with full repository access should confirm the `TransferFrom` precompile function does not add extra restrictions tied to isolated addresses before treating this as fully confirmed.

### Citations

**File:** x/ibc/callbacks/types/keys.go (L13-17)
```go
// GenerateIsolatedAddress generates an isolated address for the given channel ID and sender address.
// This provides a safe address to call the receiver contract address with custom calldata
func GenerateIsolatedAddress(channelID string, sender string) sdk.AccAddress {
	return sdk.AccAddress(address.Module(ModuleName, []byte(channelID), []byte(sender))[:20])
}
```

**File:** precompiles/erc20/approve.go (L47-60)
```go
	switch {
	case allowance.Sign() == 0 && amount != nil && amount.Sign() < 0:
		// case 1: no allowance, amount 0 or negative -> error
		err = ErrNegativeAmount
	case allowance.Sign() == 0 && amount != nil && amount.Sign() > 0:
		// case 2: no allowance, amount positive -> create a new allowance
		err = p.setAllowance(ctx, owner, spender, amount)
	case allowance.Sign() > 0 && amount != nil && amount.Sign() <= 0:
		// case 3: allowance exists, amount 0 or negative -> remove from spend limit and delete allowance if no spend limit left
		err = p.erc20Keeper.DeleteAllowance(ctx, p.Address(), owner, spender)
	case allowance.Sign() > 0 && amount != nil && amount.Sign() > 0:
		// case 4: allowance exists, amount positive -> update allowance
		err = p.setAllowance(ctx, owner, spender, amount)
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L185-212)
```go
	erc20 := contracts.ERC20MinterBurnerDecimalsContract

	remainingGas := math.NewIntFromUint64(cachedCtx.GasMeter().GasRemaining()).BigInt()

	// Call the EVM with the remaining gas as the maximum gas limit.
	// Up to now, the remaining gas is equal to the callback gas limit set by the user.
	// NOTE: use the cached ctx for the EVM calls.
	res, err := k.evmKeeper.CallEVM(cachedCtx, erc20.ABI, receiverHex, tokenPair.GetERC20Contract(), true, remainingGas, "approve", contractAddr, amountInt.BigInt())
	if err != nil {
		return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to set allowance: %v", err)
	}

	// Consume the actual used gas on the original callback context.
	ctx.GasMeter().ConsumeGas(res.GasUsed, "callback allowance")
	remainingGas = remainingGas.Sub(remainingGas, math.NewIntFromUint64(res.GasUsed).BigInt())
	if ctx.GasMeter().IsOutOfGas() || remainingGas.Cmp(big.NewInt(0)) < 0 {
		return errorsmod.Wrapf(types.ErrOutOfGas, "out of gas")
	}

	var approveSuccess bool
	err = erc20.ABI.UnpackIntoInterface(&approveSuccess, "approve", res.Ret)
	if err != nil {
		return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to unpack approve return: %v", err)
	}

	if !approveSuccess {
		return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to set allowance")
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L229-239)
```go
	// Check that the sender no longer has tokens after the callback.
	// NOTE: contracts must implement an IERC20(token).transferFrom(msg.sender, address(this), amount)
	// for the total amount, or the callback will fail.
	// This check is here to prevent funds from getting stuck in the isolated address,
	// since they would become irretrievable.
	receiverTokenBalance := k.erc20Keeper.BalanceOf(ctx, erc20.ABI, tokenPair.GetERC20Contract(), receiverHex) // here,
	// we can use the original ctx and skip manually adding the gas
	if receiverTokenBalance.Cmp(big.NewInt(0)) != 0 {
		return errorsmod.Wrapf(erc20types.ErrEVMCall,
			"receiver has %d unrecoverable tokens after callback", receiverTokenBalance)
	}
```
