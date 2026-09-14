### Title
Attacker-controlled offer bytes stored via `respond_to_offer`/`save_trade` crash `get_all_offers` for all trades — ([File: chia/wallet/wallet_request_types.py])

### Summary
The MobSF report describes a class of bug where a single attacker-controlled record (a bundle ID persisted from an uploaded app) is later rendered by a listing page without validation, and an exception during that rendering makes the *entire* listing page unavailable for every user, not just the offending record. The reachable analog in this wallet codebase is `GetAllOffersResponse.to_json_dict()`, which unconditionally calls `TradeRecord.to_json_dict_convenience()` for every stored trade, which in turn calls `Offer.from_bytes(...).summary()` on attacker-supplied offer bytes.

### Finding Description
`GetAllOffersResponse.to_json_dict()` iterates over every returned trade record and calls `tr.to_json_dict_convenience()` unconditionally — this is not gated by `file_contents` or any other flag: [1](#0-0) 

`to_json_dict_convenience()` deserializes the stored offer bytes and calls `.summary()` on them to build the JSON response: [2](#0-1) 

The offer bytes that get stored come directly from a counterparty's offer file when a wallet user calls `take_offer`/`respond_to_offer`. `respond_to_offer` builds a `TradeRecord` containing the raw, attacker-controlled `offer` (the taker's own bytes) as well as `taken_offer=bytes(offer)` — the *maker's* original, fully attacker-crafted bytes — and persists both via `save_trade`/`trade_store.add_trade_record` without ever calling `.summary()` to validate that the stored blob can be safely summarized later: [3](#0-2) [4](#0-3) 

The stored trade record's `driver_dict` and puzzle-info construction (`PuzzleInfo`, `also()`, curried "flags"/`proofs_checker` structures used elsewhere in offer-summary code) are attacker-influenced values embedded in the offer bech32 blob, as seen in the related `get_offer_summary` handler that manipulates `info.info["also"]["proofs_checker"]` via `assemble()`/`ProofsChecker.from_program()`: [5](#0-4) 

Because `get_all_offers`/`GetAllOffersResponse.to_json_dict()` re-parses and re-summarizes *every* trade record's offer bytes on every single invocation (it is not a one-time validation performed at ingest time), any one previously-accepted trade whose stored offer/taken_offer blob triggers an exception inside `Offer.summary()` (or the driver-dict/`PuzzleInfo` machinery it depends on) will cause the entire `get_all_offers` RPC call — and by extension the wallet CLI's `get_offers --summaries` and any GUI trade-history view — to fail for every trade in that page, not just the malicious one, until the offending row is manually purged from the trade DB.

### Impact Explanation
This matches the "spend-triggered transaction-processing halt" category: a single crafted offer accepted once by a wallet user permanently degrades that wallet's `get_all_offers`/trade-history RPC (and CLI `get_offers`) for all subsequent calls including unrelated trades, since the failing record is retrieved and re-processed on every listing call. This is a Medium/High-severity denial-of-service against wallet trade functionality reachable by an unprivileged offer counterparty — no consensus break, no fund theft, but a persistent partial-DoS of wallet trade UX exactly analogous to the MobSF "Scans Results" page outage.

### Likelihood Explanation
Reaching this requires an untrusted counterparty to construct an offer file with a `driver_dict`/`PuzzleInfo` structure that parses successfully enough to pass through `check_offer_validity`/`_create_offer_for_ids`/`check_for_final_modifications` (so the trade is accepted and persisted) but subsequently throws when `.summary()` (or the exact `PuzzleInfo.also()["proofs_checker"]` / `assemble()` path used by dependent summary code) is invoked on it. I was not able to fully trace `Offer.summary()`'s complete exception surface within the tool budget available — this is the main area of uncertainty. The overall shape of the flow (accept-then-repeatedly-reparse-on-list) is confirmed, but I could not verify a concrete byte sequence that passes acceptance/validation yet fails summarization.

### Recommendation
- Validate that `Offer.summary()` (and any driver-dict-dependent post-processing used by `GetAllOffersResponse`/`get_offer_summary`) succeeds on the offer bytes at the time the trade is first accepted/stored (`save_trade`/`add_trade_record`), rejecting the trade if summarization fails, rather than only ever validating at request time.
- Wrap the per-record `to_json_dict_convenience()` calls in `GetAllOffersResponse.to_json_dict()` in a try/except so a single malformed/malicious stored trade cannot break the entire listing response; return a placeholder/error marker for that specific record instead.
- Apply the same defensive parsing/try-except pattern to any other endpoint that iterates over persisted, counterparty-supplied blobs (NFT metadata URIs, DID metadata, CAT driver info) and reconstructs derived objects on every read.

### Proof of Concept
Not independently reproduced. A concrete PoC would require: (1) constructing an `Offer` bech32 blob with a `driver_dict`/`PuzzleInfo` payload that passes `check_offer_validity` and `_create_offer_for_ids`/`check_for_final_modifications` in `trade_manager.py`, but (2) causes `Offer.summary()` (or the info-manipulation logic mirrored in `wallet_rpc_api.py`'s `get_offer_summary`) to raise when re-parsed. I could not verify such a byte sequence exists within the available tool budget, so this proof-of-concept step remains unconfirmed and should be validated by a security engineer with direct code execution access before treating this as a confirmed, exploitable finding.

### Citations

**File:** chia/wallet/wallet_request_types.py (L2364-2374)
```python
@streamable
@dataclass(kw_only=True, frozen=True)
class GetAllOffersResponse(Streamable):
    offers: list[str] | None
    trade_records: list[TradeRecord]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            **super().to_json_dict(),
            "trade_records": [tr.to_json_dict_convenience() for tr in self.trade_records],
        }
```

**File:** chia/wallet/trade_record.py (L36-50)
```python
    def to_json_dict_convenience(self) -> dict[str, Any]:
        formatted = self.to_json_dict()
        formatted["status"] = TradeStatus(self.status).name
        offer_to_summarize: bytes = self.offer if self.taken_offer is None else self.taken_offer
        offer = Offer.from_bytes(offer_to_summarize)
        offered, requested, infos, _ = offer.summary()
        formatted["summary"] = {
            "offered": offered,
            "requested": requested,
            "infos": infos,
            "fees": offer.fees(),
        }
        formatted["pending"] = offer.get_pending_amounts()
        del formatted["offer"]
        return formatted
```

**File:** chia/wallet/trade_manager.py (L417-429)
```python
    async def save_trade(self, trade: TradeRecord, offer: Offer, action_scope: WalletActionScope) -> None:
        offer_name: bytes32 = offer.name()
        await self.trade_store.add_trade_record(trade, offer_name)

        # We want to subscribe to the coin IDs of all coins that are not the ephemeral offer coins
        offered_coins: set[Coin] = {value for values in offer.get_offered_coins().values() for value in values}
        non_offer_additions: set[Coin] = set(offer.additions()) ^ offered_coins
        non_offer_removals: set[Coin] = set(offer.removals()) ^ offered_coins
        await self.wallet_state_manager.add_interested_coin_ids(
            [coin.name() for coin in (*non_offer_removals, *non_offer_additions)]
        )

        action_scope.dispatch_websocket_event(self.wallet_state_manager, WebSocketEvent(name="offer_added"))
```

**File:** chia/wallet/trade_manager.py (L896-911)
```python
        trade_record: TradeRecord = TradeRecord(
            confirmed_at_index=uint32(0),
            accepted_at_time=uint64(time.time()),
            created_at_time=uint64(time.time()),
            is_my_offer=False,
            sent=uint32(0),
            offer=bytes(complete_offer),
            taken_offer=bytes(offer),
            coins_of_interest=complete_offer.get_involved_coins(),
            trade_id=complete_offer.name(),
            status=uint32(TradeStatus.PENDING_CONFIRM.value),
            sent_to=[],
            valid_times=parse_timelock_info(extra_conditions),
        )

        await self.save_trade(trade_record, offer, action_scope)
```

**File:** chia/wallet/wallet_rpc_api.py (L2011-2037)
```python
        # This is a bit of a hack in favor of returning some more manageable information about CR-CATs
        # A more general solution surely exists, but I'm not sure what it is right now
        return dataclasses.replace(
            response,
            summary=dataclasses.replace(
                response.summary,
                infos={
                    key: (
                        PuzzleInfo(
                            {
                                **info.info,
                                "also": {
                                    **info.info["also"],
                                    "flags": ProofsChecker.from_program(
                                        UnknownPuzzle(
                                            known_program=Program(assemble(info.info["also"]["proofs_checker"]))
                                        )
                                    ).flags,
                                },
                            }
                        )
                        if "also" in info.info and "proofs_checker" in info.info["also"]
                        else info
                    )
                    for key, info in response.summary.infos.items()
                },
            )
```
