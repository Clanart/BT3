### Title
Nil-pointer dereference in `IBCReceivePacketCallback` via unchecked `erc20Keeper.BalanceOf()` result causes a validator-triggerable panic during IBC packet processing - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
The Convex `_claimRewards()` bug is a class of "unchecked/assumed-safe external read used directly in a critical accounting check." The same bug class exists in `Kirstentat/push-chain-evm--012`'s IBC EVM-callback flow: `ContractKeeper.IBCReceivePacketCallback` calls `k.erc20Keeper.BalanceOf(...)` and immediately calls `.Cmp()` on the result without checking for `nil`, even though `Keeper.BalanceOf` is explicitly documented/implemented to return `nil` on failure.

### Finding Description
`x/erc20/keeper/evm.go` `BalanceOf` returns `nil` whenever the underlying EVM call fails, the ABI unpack fails, or the unpacked type assertion fails: [1](#0-0) 

This function is called at the very end of `IBCReceivePacketCallback` to validate that the isolated receiver address's ERC20 balance is fully drained after the destination contract callback executes: [2](#0-1) 

The result, `receiverTokenBalance`, is a `*big.Int` that is directly dereferenced via `.Cmp(big.NewInt(0))` with no nil check. If `BalanceOf` returns `nil` (e.g., because the token pair's ERC20 contract self-destructed during the callback execution, the token doesn't implement `balanceOf` in the expected ABI shape, or the ERC20 call reverts/fails for any deterministic reason after the callback function runs), this causes a Go nil-pointer dereference panic.

This flow is reachable by any unprivileged user: any account can send an ICS-20 transfer with a `dest_callback` memo pointing to an arbitrary destination contract (see `x/ibc/callbacks/README.md`), and the destination contract is unprivileged/attacker-controlled code. An attacker-controlled contract executed during the callback (`res, err = k.evmKeeper.CallEVMWithData(cachedCtx, receiverHex, &contractAddr, cbData.Calldata, true, remainingGas)`, line 215) can, for example, self-destruct the token pair's underlying native-ERC20 contract (if `pair.IsNativeERC20()`), causing the subsequent `BalanceOf` call at line 234 to fail and return `nil`.

Because packet processing is part of ordinary transaction/block execution (`OnRecvPacket` -> IBC callbacks middleware -> `IBCReceivePacketCallback`), a panic here occurs deterministically on every validator processing the block containing this packet. Whether the panic is caught by `recover()` somewhere higher up the ABCI/IBC callbacks-middleware stack was not confirmed in this codebase (no top-level `recover()` was found guarding `ProcessCallback`/`OnRecvPacket` in the searched files); if unrecovered, this is a chain halt via panic reachable by an ordinary user. Even if IBC-go's callback middleware recovers panics for out-of-gas-style errors only (as documented for gas-limit protection), a generic nil-pointer panic is a different failure mode not addressed by that gas-based recovery, and I could not verify a general-purpose recover wrapping this specific call path within this repository's code.

### Impact Explanation
If the panic propagates unrecovered, this causes non-deterministic node crashes across all validators processing the same packet -> a critical, unprivileged-user-triggerable chain halt. This matches the "chain halt ... an unprivileged user can trigger through ordinary transaction ... flow" critical impact category. Even in the best case where IBC-go recovers the panic and turns it into a packet processing failure, the underlying invariant intended by this check (verifying full token drain, preventing "funds getting stuck in the isolated address ... irretrievable" per the code's own comment) is silently bypassed, since a panic/recover short-circuits before the intended error (`ErrEVMCall`, "receiver has unrecoverable tokens") is ever returned - potentially leaving the invariant unchecked and tokens permanently stuck at the isolated address without the intended safety guard firing.

### Likelihood Explanation
Reachable by any unprivileged actor who can construct an ICS-20 transfer with a `dest_callback` memo (a standard, user-facing feature per this repo's own IBC callbacks documentation) targeting an attacker-deployed EVM contract. The attacker fully controls the callback's calldata/logic (`cbData.Calldata` executed via `CallEVMWithData`), which is exactly the vector needed to induce the ERC20 contract to self-destruct or otherwise break subsequent `balanceOf` calls before the vulnerable check runs.

### Recommendation
In `IBCReceivePacketCallback` (and any other caller of `erc20Keeper.BalanceOf`), explicitly check for a `nil` return value before calling `.Cmp`, and treat `nil` as an error condition (e.g., return `errorsmod.Wrapf(erc20types.ErrEVMCall, "failed to query receiver token balance")`). More robustly, change `Keeper.BalanceOf` to return `(*big.Int, error)` instead of silently returning `nil` on failure, forcing all call sites to handle the failure path explicitly, consistent with the recommendation in the source report to validate the call's success rather than assuming a value is safely usable.

### Proof of Concept
Conceptual PoC (cannot be executed in this environment; based on static code tracing):
1. Attacker registers/uses an existing native-ERC20 token pair whose ERC20 contract includes a `selfdestruct`-capable admin/owner function reachable by the attacker (or otherwise deploys their own token pair via governance/registration flow where permitted, or targets an already vulnerable existing token contract).
2. Attacker sends an ICS-20 transfer to the chain with memo:
```json
{"dest_callback": {"address": "<attacker_contract>", "gas_limit": "<sufficient_gas>"}}
```
3. `attacker_contract`'s callback entrypoint calls `transferFrom` for the required amount (satisfying the pre-check) and then triggers the token contract's `selfdestruct` (if the token exposes such a path) or otherwise renders `balanceOf` calls on that address failing/reverting for the isolated receiver.
4. Execution reaches `x/ibc/callbacks/keeper/keeper.go:234`, `k.erc20Keeper.BalanceOf(...)` returns `nil`.
5. Line 236, `receiverTokenBalance.Cmp(big.NewInt(0))` panics with a nil pointer dereference.

Note: I was unable to fully confirm from the indexed code whether IBC-go's callback middleware (`ProcessCallback`) wraps this call with a general `recover()` that would prevent a full chain halt; this repository's own code does not contain such a recover around this call path. This gap should be verified in a live/full-repo Devin session, since the index may not include the complete IBC-go integration/vendor code for `ProcessCallback`.

### Citations

**File:** x/erc20/keeper/evm.go (L138-158)
```go
func (k Keeper) BalanceOf(
	ctx sdk.Context,
	abi abi.ABI,
	contract, account common.Address,
) *big.Int {
	res, err := k.evmKeeper.CallEVM(ctx, abi, types.ModuleAddress, contract, false, nil, "balanceOf", account)
	if err != nil {
		return nil
	}

	unpacked, err := abi.Unpack("balanceOf", res.Ret)
	if err != nil || len(unpacked) == 0 {
		return nil
	}

	balance, ok := unpacked[0].(*big.Int)
	if !ok {
		return nil
	}

	return balance
```

**File:** x/ibc/callbacks/keeper/keeper.go (L234-239)
```go
	receiverTokenBalance := k.erc20Keeper.BalanceOf(ctx, erc20.ABI, tokenPair.GetERC20Contract(), receiverHex) // here,
	// we can use the original ctx and skip manually adding the gas
	if receiverTokenBalance.Cmp(big.NewInt(0)) != 0 {
		return errorsmod.Wrapf(erc20types.ErrEVMCall,
			"receiver has %d unrecoverable tokens after callback", receiverTokenBalance)
	}
```
