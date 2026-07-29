## Analysis

The GMX bug is a classic **"snapshot state → external step → compare-with-stale-invariant"** race: an attacker injects value into a shared, address-scoped location between the moment a request is recorded and the moment it is finalized, corrupting an equality/threshold check that the finalization logic relies on.

The closest reachable analog in this Cosmos EVM fork is in the **ICS-20 destination callback flow** implemented in `x/ibc/callbacks/keeper/keeper.go`, specifically `ContractKeeper.IBCReceivePacketCallback`.

### Title
Griefable exact-zero-balance invariant on deterministic "isolated address" permanently freezes ICS-20 auto-callback funds - (File: `x/ibc/callbacks/keeper/keeper.go`)

### Summary
`IBCReceivePacketCallback` routes IBC-received tokens through a deterministic, key-less "isolated address" (`types.GenerateIsolatedAddress(channel, sender)`), approves an ERC20 allowance for the target contract, invokes the contract, and then enforces that the isolated address's ERC20 balance is **exactly zero** afterward: [1](#0-0) [2](#0-1) 

This is structurally identical to the GMX pattern: state (the isolated address and its expected token balance) is established in one step, an external/async action happens (the contract call, potentially several blocks/packets later since the isolated address is fixed for every packet from a given `(channel, sender)` pair), and finalization blindly trusts a comparison (`balance == 0`) that assumes nobody else touched that address in between.

### Finding Description
`GenerateIsolatedAddress(channel, sender)` is fully deterministic and public — any unprivileged party can compute it in advance for any `(destChannel, sender)` pair (the packet sender string is visible/predictable, e.g. a well-known bridging relayer or a targeted victim's address) before the corresponding IBC packet with callback memo is ever relayed: [3](#0-2) 

An attacker can call the ERC20 precompile/contract for `tokenPair.GetERC20Contract()` and `transfer` a small amount of that token directly to the isolated address at any time — no permission, no packet, no callback required (see `precompiles/erc20/tx.go` `transfer`): [4](#0-3) 

When the legitimate packet later arrives and the destination contract correctly performs `transferFrom(isolatedAddr, contract, amount)` for the full packet amount, the isolated address still holds the attacker-injected dust, so: [5](#0-4) 

`receiverTokenBalance != 0` is always true from that point forward, so `IBCReceivePacketCallback` returns `types.ErrEVMCall` every single time a packet is received for that `(channel, sender)` pair — because the isolated address is reused for **every future packet from the same sender on the same channel**, this is not a one-shot griefing but a **permanent** condition.

Critically, `GenerateIsolatedAddress` produces a hash-derived address with **no corresponding private key** (analogous to a module account). There is no signed transaction path by which the legitimate owner can move the stuck balance out of that address. Combined with the fact that the exact-zero check can never again be satisfied once dust is injected, any tokens that land at that address (the attacker's dust, and — depending on how `ibc-go`'s callbacks middleware treats destination-callback failures — potentially the user's own transferred principal) become **permanently unrecoverable**.

### Impact Explanation
This satisfies the Critical "permanent freezing/locking of user funds" bar: an unprivileged attacker, using only an ordinary ERC20 `transfer` call, can permanently disable the auto-swap callback for any `(channel, sender)` combination they can predict, and can strand ERC20-representation tokens at an address that has no controlling key and can never pass the invariant check required to release them. This is reachable purely through the production IBC-receive + EVM-callback flow, requires no privileged relayer/validator behavior, and directly corrupts spendable user value (tokens become permanently inaccessible).

### Likelihood Explanation
Likelihood is moderate-to-high for griefing a *known* target: `(destChannel, sender)` pairs used for cross-chain-callback transfers are often predictable in advance (bridging integrations, known user addresses, or an attacker can simply pre-poison an address before initiating their own future transfer to "test" the bug, which already proves total, permanent denial of the feature for themselves and demonstrates the freeze). I was not able to fully verify, within the available tool budget, the exact `ibc-go v10` callbacks-middleware semantics for how a destination-callback error propagates (i.e., whether it merely skips the contract hook while leaving the already-minted ERC20 balance recoverable at the isolated address only through this same broken code path, or whether it also fails the packet acknowledgement). Either way, the isolated address's ERC20 balance becomes permanently stuck behind a strict-equality check that an attacker can trivially and permanently poison, and that address has no private key to allow any alternate recovery path — this is the concrete, reachable, corrupted invariant.

### Recommendation
- Do not require `receiverTokenBalance == 0`; instead snapshot the isolated address balance *before* the `approve`/contract call and compare against the pre-call balance minus the expected packet `amount` (mirroring the GMX report's own recommendation of comparing before/after deltas rather than trusting an absolute value).
- Alternatively, sweep any residual balance at the isolated address back to the original `receiver`/`data.Sender` instead of erroring, so that unrelated/injected dust cannot block or permanently freeze legitimate transferred funds.
- Ensure there is a governance- or user-triggerable recovery path (e.g., a keeper method allowing the real receiver to claim any residual balance at their isolated address) since the isolated address has no private key.

### Proof of Concept
1. Attacker observes/predicts a future ICS-20 callback transfer will use `sender = S` over `channel = C`, targeting `tokenPair` denom `D` with ERC20 contract `T`.
2. Attacker computes `isolatedAddr = GenerateIsolatedAddress(C, S)` (public, deterministic derivation) and calls `T.transfer(isolatedAddr, 1)` via the ERC20 precompile — a completely ordinary, permissionless transaction.
3. Victim's IBC transfer with a destination-callback memo is relayed; `x/erc20` middleware credits `isolatedAddr` with the transferred ERC20 amount; `IBCReceivePacketCallback` approves and invokes the destination contract, which correctly `transferFrom`s the full packet `amount` out.
4. `k.erc20Keeper.BalanceOf(ctx, ..., isolatedAddr)` is now `1` (the attacker's dust), not `0`, so the function returns `ErrEVMCall` at `keeper.go:236-239` every time, permanently, for any future packet from `(C, S)`.
5. Because `isolatedAddr` has no private key, the attacker's own dust — and any tokens that remain stuck there through this failing path — cannot be moved by any signed transaction, resulting in a permanent freeze of value at that address.

### Citations

**File:** x/ibc/callbacks/keeper/keeper.go (L145-156)
```go
	// Generate secure isolated address from sender.
	isolatedAddr := types.GenerateIsolatedAddress(packet.GetDestChannel(), data.Sender)
	isolatedAddrHex := common.BytesToAddress(isolatedAddr.Bytes())

	acc := k.authKeeper.NewAccountWithAddress(ctx, receiver)
	k.authKeeper.SetAccount(ctx, acc)

	// Ensure receiver address is equal to the isolated address.
	if receiverHex.Cmp(isolatedAddrHex) != 0 {
		return errorsmod.Wrapf(types.ErrInvalidReceiverAddress, "expected %s, got %s", isolatedAddrHex.String(), receiverHex.String())
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

**File:** precompiles/erc20/tx.go (L69-83)
```go
func (p *Precompile) transfer(
	ctx sdk.Context,
	contract *vm.Contract,
	stateDB vm.StateDB,
	method *abi.Method,
	from, to common.Address,
	amount *big.Int,
) (data []byte, err error) {
	coins := sdk.Coins{{Denom: p.tokenPair.Denom, Amount: math.NewIntFromBigInt(amount)}}

	msg := banktypes.NewMsgSend(from.Bytes(), to.Bytes(), coins)

	if err = msg.Amount.Validate(); err != nil {
		return nil, err
	}
```
