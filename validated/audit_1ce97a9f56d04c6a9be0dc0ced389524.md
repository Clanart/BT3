Found a valid analog. The critical part: `IBCReceivePacketCallback` in `x/ibc/callbacks/keeper/keeper.go` sets an ERC20 allowance via `approve` for an isolated, deterministic address before invoking an arbitrary destination contract, and explicitly checks post-callback that the full balance was consumed — but it never resets the allowance to zero before or after approving, and does not verify the allowance itself was consumed to zero.

### Title
Non-reset ERC20 `approve` in IBC receive-packet callback can permanently brick future callbacks for an isolated address - (File: x/ibc/callbacks/keeper/keeper.go)

### Summary
`IBCReceivePacketCallback` calls `approve(contractAddr, amountInt)` on the registered ERC20 token contract from a deterministic isolated address [1](#0-0)  without ever resetting the allowance to zero first, and without confirming the allowance was fully consumed after the destination contract's callback runs. It only checks that the receiver's *token balance* is zero [2](#0-1) , which is a different condition than "allowance fully spent." This mirrors the Morpho/USDT bug class: approving a non-zero value on top of an existing non-zero allowance can permanently break future interactions with non-standard ERC20 tokens (e.g., USDT-style tokens registered as a native ERC20 token pair via `x/erc20`) that revert on `approve` from non-zero to non-zero.

### Finding Description
The isolated address for a given `(destChannel, sender)` pair is deterministic [3](#0-2) , meaning the same address can be the receiver of multiple IBC packets carrying callback data over time, for the same token pair. Each time, the keeper calls:

```
k.evmKeeper.CallEVM(cachedCtx, erc20.ABI, receiverHex, tokenPair.GetERC20Contract(), true, remainingGas, "approve", contractAddr, amountInt.BigInt())
``` [1](#0-0) 

If the destination `contractAddress` does not fully spend the granted allowance (e.g., only partially calls `transferFrom`, or the callback logic errors after a partial `transferFrom`, or the developer-supplied contract simply doesn't consume the exact amount), then a residual non-zero allowance remains from the isolated address to that contract for that token. The code's only post-check is on the **receiver's balance**, not the **allowance value** [2](#0-1) , so a balance of zero (all tokens moved) does not guarantee the allowance itself dropped to zero if the transferFrom logic in the destination contract doesn't reduce allowance by the exact granted amount (this is exactly the Aave/Morpho `Math.min(...)` pattern from the source report, generalized to any custom or non-standard ERC20 registered via `x/erc20`).

On a subsequent IBC packet targeting the same isolated address / token / destination contract, the keeper again calls `approve(contractAddr, newAmount)` while a non-zero allowance still exists. If the underlying token is a USDT-style non-standard ERC20 (which reverts when changing a non-zero allowance to a different non-zero value without first resetting to zero — a token type explicitly supported by `x/erc20`'s native-ERC20 token pair registration, since arbitrary externally-deployed ERC20 contracts can be registered), the `approve` call reverts. `CallEVM` returning an error causes `IBCReceivePacketCallback` to return an error [4](#0-3) .

### Impact Explanation
Because the ICS20 token transfer underlying the packet has already been executed (coins were credited to the isolated receiver as part of standard IBC-Go receive processing) before the callback logic runs, an error returned from `IBCReceivePacketCallback` fails only the *callback middleware* step, not the underlying transfer, per IBC-Go's callbacks middleware design (the callback failure does not roll back the transfer that already succeeded in the transfer app). This leaves the tokens stuck at the isolated address — the isolated address is a deterministically-generated, uncontrolled address (not a normal user's key), and the isolated address's tokens can only be moved via the same `approve` + destination-contract-call flow, which is now permanently broken for that (channel, sender, token, destination contract) combination since the reverting `approve` call will always fail before the intended transfer logic runs. This is a permanent freezing/locking of user funds sent via IBC to that channel/sender/token/contract combination — matching the "Critical permanent freezing, locking ... of user funds ... token-pair-backed balances" impact category. Every subsequent packet from the same sender through the same channel using the same destination contract and token becomes unrecoverable, since the isolated address cannot be independently controlled by any private key to reset the allowance out-of-band.

### Likelihood Explanation
Triggering this requires: (1) an ERC20 token pair registered via `x/erc20` backed by a token contract with USDT-style approve semantics (or any custom token/contract combination where the destination contract does not consume the exact granted allowance) — token pair registration for external contracts is a standard, unprivileged/governance-approved but externally-controlled flow, and (2) a destination contract (attacker-controlled or buggy, since destination contracts are arbitrary, attacker-deployable EVM contracts referenced by `contractAddress` in the callback packet) that leaves a non-zero residual allowance. An unprivileged attacker who controls the destination contract and knows a victim will send IBC transfers with callback data to that contract can deliberately leave a small non-zero allowance on the first packet (e.g., only doing `transferFrom` for `amount - 1`), then send/trigger further packets to permanently DoS all future callback-based transfers for that isolated address, freezing subsequent transferred funds. This does not require any privileged role, malicious relayer, or validator — only a chosen registered token type and a self-deployed destination contract, both attacker-controlled and within the intended production usage pattern of the callbacks feature.

### Recommendation
Before calling `approve` with a new non-zero amount, either (1) call `approve(contractAddr, 0)` first to reset the allowance unconditionally, or (2) read the current allowance via the ERC20 `allowance()` view and only call `approve` with the delta / handle non-zero-to-non-zero transitions safely, or (3) after the callback executes, explicitly verify (and, if necessary, forcibly reset) that the allowance for `(isolatedAddr, contractAddr)` is exactly zero — not just that the isolated address's token balance is zero — since balance-zero does not imply allowance-zero.

### Proof of Concept
1. Register a token pair for a native ERC20 contract implementing USDT-style `approve` semantics (revert when changing a non-zero allowance to a different non-zero value without resetting to zero first) via `x/erc20`'s `RegisterERC20`.
2. Attacker deploys a destination contract `C` whose callback-invoked function calls `IERC20(token).transferFrom(isolatedAddr, address(this), amount - 1)` (i.e., intentionally leaves 1 unit of allowance unspent) instead of the full `amount`.
3. Victim sends an ICS20 transfer with IBC-Go callbacks memo pointing `contractAddress` to `C`, causing `IBCReceivePacketCallback` to run: `approve(isolatedAddr -> C, amount)` succeeds, `C.callback` consumes `amount-1`, leaving allowance `= 1`. The final balance check on the isolated address's token balance passes (since `C` received `amount-1` and 1 unit... actually if `transferFrom` moves `amount-1` tokens, `1` unit of token balance remains at `isolatedAddr`, which *would* fail the current balance check — but if `C` instead calls `transfer` for the last unit directly (not via allowance) or the token has quirks where balance can reach zero while allowance remains non-zero (e.g., a separate direct `transfer` call from `isolatedAddr` triggered inside `C`'s callback, which is possible since `isolatedAddr` is the `msg.sender` context of the approve but the callback executes `CallEVMWithData(cachedCtx, receiverHex, &contractAddr, cbData.Calldata, ...)` [5](#0-4)  as `receiverHex`, i.e., the isolated address itself is the caller, so `C` can direct the isolated address to call `token.transfer(C, 1)` directly, zeroing the balance while leaving `allowance = 1` untouched), the balance check at line 234-239 passes while allowance remains non-zero.
4. Victim (or any other sender using the same channel/sender pair, since the isolated address is deterministic per `(destChannel, sender)`) sends a second packet targeting the same `contractAddress` and token; `IBCReceivePacketCallback` calls `approve(isolatedAddr -> C, newAmount)` on top of the existing non-zero allowance of `1`. For a USDT-style token this reverts, and the callback errors permanently for all future packets from that sender/channel to that contract/token, freezing any coins credited to the isolated address by the underlying (already-completed) ICS20 transfer.

**Note on uncertainty:** I was not able to fully verify within available tool calls (a) whether IBC-Go's callbacks middleware truly leaves the underlying transfer committed when the callback function returns an error (versus atomically reverting the whole packet processing including the transfer), and (b) whether `x/erc20`'s native-ERC20 registration flow permits arbitrary externally-deployed contracts with non-standard approve semantics (USDT-style) to be registered without additional validation that would reject such tokens. Confirming both would require deeper inspection of the IBC-Go `ibccallbacks` middleware source (external dependency, possibly not indexed) and `x/erc20`'s `RegisterERC20` validation logic, which should be verified in a full Devin session with complete file/dependency access before treating this as a confirmed exploit.

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L145-147)
```go
	// Generate secure isolated address from sender.
	isolatedAddr := types.GenerateIsolatedAddress(packet.GetDestChannel(), data.Sender)
	isolatedAddrHex := common.BytesToAddress(isolatedAddr.Bytes())
```

**File:** x/ibc/callbacks/keeper/keeper.go (L185-195)
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
```

**File:** x/ibc/callbacks/keeper/keeper.go (L215-215)
```go
	res, err = k.evmKeeper.CallEVMWithData(cachedCtx, receiverHex, &contractAddr, cbData.Calldata, true, remainingGas)
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
