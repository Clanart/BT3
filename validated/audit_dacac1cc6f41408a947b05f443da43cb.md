## Title
Attacker can force a winning searcher's `AuctionEntryPoint` nonce to be consumed while the underlying call reverts, griefing KIP-249 auction bidders — (File: `contracts/testing/system_contracts/AuctionEntryPointMock.sol`)

### Summary
Kaia's KIP-249 auction system (`kaiax/auction`) implements a searcher-nonce-based `AuctionEntryPoint.call(AuctionTx)` flow that mirrors the exact bug class described in the external report for `ERC2771Forwarder`/`TransactionForwarder_v1`: the searcher's on-chain nonce is consumed via `_useNonce(searcher)` **before** the wrapped external call executes and **regardless of whether that call succeeds**. Any party who can influence chain state ahead of the bid's inclusion (e.g. another unprivileged transaction sender) can force the winning searcher's wrapped call to revert while the searcher's nonce is still burned, invalidating pending/future signed bids and wasting the searcher's bid/deposit.

### Finding Description
The reference `AuctionEntryPointMock.sol` (used for CN test wiring) preserves — in commented form — the real production logic of `IAuctionEntryPoint.call`: [1](#0-0) 

```solidity
function call(AuctionTx calldata auctionTx) external onlyProposer {
    // 1. Verify input integrity
    if (!_verifyInputIntegrity(auctionTx)) revert();
    // // 2. Take bid first
    // if (!_checkAndTakeBid(searcher, auctionTx.bid, callGasLimit)) revert();
    // // 3. Execute call and refund execution gas
    // uint256 nonce = _useNonce(searcher);
    // (bool success, ) = auctionTx.to.call{gas: callGasLimit}(auctionTx.data);
    // if (success) {
    //     emit Call(searcher, nonce);
    // } else {
    //     emit CallFailed(searcher, nonce);
    // }
```

This is exactly the pattern flagged in the Hats report for `ERC2771Forwarder::_execute`: `_useNonce(searcher)` runs unconditionally **before** the `.call{gas: callGasLimit}(auctionTx.data)`, and both success and failure paths (`Call` / `CallFailed` events) leave the nonce incremented. The corresponding production ABI confirms this design is real, not test-only: the entry-point exposes a `UseNonce(address searcher, uint256 nonce)` event and a `CallFailed(address sender, uint256 nonce)` event, plus a `getNoncesAndDeposits` view used to track per-searcher state: [2](#0-1) [3](#0-2) 

The searcher's signed `AuctionTx` (containing `to`, `data`, `callGasLimit`, `nonce`, `bid`) is submitted via `auction_submitBid` and, once selected as the highest bid for a target transaction, is wrapped into a `BidTx` and executed by the block proposer immediately after the target transaction: [4](#0-3) 

Because the bid is public once it circulates to/through the `Auctioneer` and is included as calldata in a broadcast `BidTx`, and because `to`/`data`/`callGasLimit` describe an external call whose success depends on mutable on-chain state, any unprivileged actor can submit an ordinary transaction ahead of the target/bid transaction in the same block to change state (e.g., drain a balance, change an allowance/price, hit a cap) such that `auctionTx.to.call{gas: callGasLimit}(auctionTx.data)` reverts. Since `_useNonce(searcher)` already ran, the searcher's nonce is burned and `CallFailed` is emitted — the searcher gets neither the intended effect nor a retry with the same nonce, exactly mirroring the forwarder griefing pattern in the report.

### Impact Explanation
A searcher's future bids (all of which are pre-signed against a specific `nonce` per KIP-249, analogous to the forwarder's EIP-712 nonce) become invalid the moment their nonce is griefed, since `getNoncesAndDeposits` / `nonce == nonces(sender)` checks (visible in the commented `_verifyInputIntegrity`) will now reject them. This causes denial of service against a specific searcher's auction participation and can also cause loss of a taken bid amount/deposit if `_checkAndTakeBid` runs unconditionally before the nonce/`call` step (as ordered in the mock). This is a state-machine level unauthorized value/side-effect issue reachable by any unprivileged transaction sender who is not the searcher, matching the class of "acceptance of a transaction whose signed nonce is consumed despite failed intended execution."

### Likelihood Explanation
This requires only an ordinary transaction from any account (no special privilege, no consensus/network role) that changes state relevant to the searcher's intended call target before the `BidTx` executes — a standard front-running/griefing capability any Kaia transaction sender already has via public mempool visibility of bids/target transactions and Auctioneer-distributed bid data. The bug is structural (nonce use precedes success check) rather than a rare edge case, so any bid whose wrapped call is state-dependent is exploitable.

### Recommendation
Only consume/increment the searcher's `AuctionEntryPoint` nonce (and only take the bid amount) after confirming that `auctionTx.to.call{gas: callGasLimit}(auctionTx.data)` succeeded, or provide an explicit non-reverting failure path that still allows the searcher to resubmit with the same nonce. Alternatively, decouple bid/deposit consumption from nonce consumption so a reverted wrapped call does not retire the searcher's nonce.

### Proof of Concept
1. Searcher signs `AuctionTx{nonce: N, to: T, data: D, callGasLimit: G, bid: B}` and submits it via `auction_submitBid`; it wins the bid for `targetTxHash`.
2. Attacker observes the pending bid/target tx pair (public via mempool/gossip) and determines that `T.D` will revert if some state variable changes (e.g., available liquidity, allowance, cap).
3. Attacker submits an ordinary transaction, ordered before the target transaction in the same block, that flips the relevant state so `T.D` will revert.
4. Block proposer includes target tx, then `BidTx` calling `AuctionEntryPoint.call(auctionTx)`; per the (commented but authoritative) production logic, `_useNonce(searcher)` executes, then `T.call{gas:G}(D)` reverts; `CallFailed(searcher, N)` is emitted.
5. Searcher's nonce is now `N+1` on-chain even though their intended call never executed, invalidating any other bid signed with nonce `N` and forcing a resubmission cycle, at the cost of any deposit/bid amount already taken.

### Citations

**File:** contracts/testing/system_contracts/AuctionEntryPointMock.sol (L54-73)
```text
    function call(AuctionTx calldata auctionTx) external onlyProposer {
        // 1. Verify input integrity
        if (!_verifyInputIntegrity(auctionTx)) revert();

        // // 2. Take bid first
        // if (!_checkAndTakeBid(searcher, auctionTx.bid, callGasLimit)) revert();

        // // 3. Execute call and refund execution gas
        // uint256 nonce = _useNonce(searcher);
        // (bool success, ) = auctionTx.to.call{gas: callGasLimit}(auctionTx.data);
        // if (success) {
        //     emit Call(searcher, nonce);
        // } else {
        //     emit CallFailed(searcher, nonce);
        // }

        // // 4. Refund gas to the proposer
        // if (!_payGas(searcher, initialGas)) revert();
        count++;
    }
```

**File:** contracts/bindings/auction/Kip249.go (L779-784)
```go
// IAuctionEntryPointCallFailed represents a CallFailed event raised by the IAuctionEntryPoint contract.
type IAuctionEntryPointCallFailed struct {
	Sender common.Address
	Nonce  *big.Int
	Raw    types.Log // Blockchain specific contextual infos
}
```

**File:** contracts/bindings/auction/Kip249.go (L1322-1327)
```go
// IAuctionEntryPointUseNonce represents a UseNonce event raised by the IAuctionEntryPoint contract.
type IAuctionEntryPointUseNonce struct {
	Searcher common.Address
	Nonce    *big.Int
	Raw      types.Log // Blockchain specific contextual infos
}
```

**File:** kaiax/auction/impl/getter.go (L27-69)
```go
func (a *AuctionModule) GetBidTxGenerator(tx *types.Transaction, bid *auction.Bid) *builder.TxOrGen {
	gen := func(nonce uint64) (*types.Transaction, error) {
		var (
			chainId           = a.InitOpts.ChainConfig.ChainID
			signer            = types.LatestSignerForChainID(chainId)
			auctionEntryPoint = a.bidPool.GetAuctionEntryPoint()
			key               = a.InitOpts.NodeKey
		)

		data, err := system.EncodeAuctionCallData(bid, a.bidPool.GetAuctionEntryPointVersion())
		if err != nil {
			return nil, err
		}

		if bid.GetGasLimit() == 0 {
			gasLimit, err := a.bidPool.getBidTxGasLimit(bid)
			if err != nil {
				return nil, err
			}
			bid.SetGasLimit(gasLimit)
		}

		tx, err := types.NewTransactionWithMap(types.TxTypeEthereumDynamicFee, map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      nonce,
			types.TxValueKeyTo:         &auctionEntryPoint,
			types.TxValueKeyAmount:     common.Big0,
			types.TxValueKeyData:       data,
			types.TxValueKeyGasLimit:   bid.GetGasLimit(),
			types.TxValueKeyGasFeeCap:  tx.GasFeeCap(),
			types.TxValueKeyGasTipCap:  tx.GasTipCap(),
			types.TxValueKeyAccessList: types.AccessList{},
			types.TxValueKeyChainID:    chainId,
		})
		if err != nil {
			return nil, err
		}

		err = tx.Sign(signer, key)

		return tx, err
	}

	return builder.NewTxOrGenFromGen(gen, bid.Hash())
```
