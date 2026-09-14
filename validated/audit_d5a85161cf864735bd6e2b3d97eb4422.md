### Title
Unhandled exception (crash) when parsing a malformed offer's dummy settlement solution - (File: chia/wallet/trading/offer.py)

### Summary
`Offer.from_spend_bundle()` reconstructs `requested_payments` from the dummy `CoinSpend`s embedded in an offer file by iterating the CLVM solution and blindly converting `payment_group.first()` into a `bytes32`. A malicious offer counterparty can craft the solution so that `payment_group.first()` is a CLVM pair (cons) instead of an atom, making `.as_atom()` return `None`. Passing `None` into `bytes32(...)` raises an unhandled `TypeError`, crashing the parsing path before any consensus/signature validation ever runs — a close analog to the reported GPAC `ctts_box_write` NULL-pointer dereference, where a crafted file field expected to hold a concrete value is null/absent and dereferenced without a check.

### Finding Description
When an offer file is loaded, `Offer.from_bytes()` → `Offer.from_spend_bundle()` treats any coin spend whose `coin.parent_coin_info == bytes32.zeros` as a "dummy" settlement spend encoding the requested payments, and parses its solution directly: [1](#0-0) 

```python
if coin_spend.coin.parent_coin_info == bytes32.zeros:
    notarized_payments: list[NotarizedPayment] = []
    for payment_group in Program.from_serialized(coin_spend.solution).as_iter():
        nonce = bytes32(payment_group.first().as_atom())
        payment_args_list = payment_group.rest().as_iter()
        ...
``` [2](#0-1) 

`coin_spend.solution` here is fully attacker-controlled CLVM bytes taken from the offer blob (`Offer.from_bech32` → `try_offer_decompression` → `from_bytes`/`from_compressed` → `from_spend_bundle`). Nothing validates that `payment_group.first()` is an atom before calling `.as_atom()`; if it's a cons pair, `.as_atom()` returns `None`, and `bytes32(None)` throws a `TypeError` that is not a `ValueError`, so it propagates past the only guard present in the CLI (`except ValueError`) in `take_offer()`: [3](#0-2) 

The same unguarded call path is reachable from local wallet RPC endpoints that accept a raw offer string and parse it before any further checks: `TakeOffer.parsed_offer` (used by `WalletRpcApi.take_offer`), `WalletRpcApi.check_offer_validity`, and `WalletRpcApi.get_offer_summary`: [4](#0-3) [5](#0-4) 

None of these call sites validate the shape of the settlement solution before `Offer.from_spend_bundle()` runs.

### Impact Explanation
Any wallet user or offer counterparty can hand another user a crafted offer file/bech32 blob that triggers an unhandled `TypeError` the moment it is examined (`examine_only`), summarized (`get_offer_summary`), validated (`check_offer_validity`), or taken (`take_offer`) — before signatures or on-chain state are checked. This halts the transaction-processing request for that offer, denying service to the local wallet RPC caller/CLI user attempting to interact with the offer. It does not, on its own, move funds or forge assets, so it is scoped to a spend/offer-triggered processing halt.

### Likelihood Explanation
Likelihood is high for triggering the crash: constructing an offer file with a non-atom first element in the dummy settlement's payment group requires no privileges, no signature, and no network access — only crafting CLVM bytes, which any wallet user can do with standard tooling. The victim only needs to open/examine/take the offer.

### Recommendation
In `Offer.from_spend_bundle()`, validate that `payment_group.first()` is an atom before calling `.as_atom()`/`bytes32(...)`, and raise a caught `ValueError` (or a dedicated `OfferParsingError`) on malformed structure instead of allowing a raw `TypeError`/`AttributeError` to propagate. Additionally, widen the exception handling around `Offer.from_bech32`/`Offer.from_bytes` at all call sites (CLI `take_offer`, `check_offer_validity`, `get_offer_summary`, `take_offer` RPC) to catch generic parsing failures, not just `ValueError`.

### Proof of Concept
1. Build a `WalletSpendBundle` containing one `CoinSpend` whose `coin.parent_coin_info == bytes32.zeros` (marking it as the offer's dummy settlement spend).
2. Set that coin spend's `solution` to a CLVM program whose first payment group is `((a . b) ...)` — i.e., a program where the first element of the group is a cons pair, not an atom (e.g. assemble `((( (1 . 2) (0xAA... 1000 ())) ))`-style solution).
3. Serialize via `Offer(...).to_bech32()` semantics (or hand-construct the equivalent bytes) and give the resulting offer string to a victim.
4. Victim calls `chia wallet take_offer <file>` (or the `check_offer_validity`/`get_offer_summary` RPC) — `Offer.from_bech32` → `Offer.from_spend_bundle` executes `bytes32(payment_group.first().as_atom())`, `as_atom()` returns `None`, and `bytes32(None)` raises `TypeError`, which is uncaught by the surrounding `except ValueError`, aborting the offer-processing request.

### Citations

**File:** chia/wallet/trading/offer.py (L634-663)
```python
    @classmethod
    def from_spend_bundle(cls, bundle: WalletSpendBundle) -> Offer:
        # Because of the `to_spend_bundle` method, we need to parse the dummy CoinSpends as `requested_payments`
        requested_payments: dict[bytes32 | None, list[NotarizedPayment]] = {}
        driver_dict: dict[bytes32, PuzzleInfo] = {}
        leftover_coin_spends: list[CoinSpend] = []
        for coin_spend in bundle.coin_spends:
            driver = match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))
            if driver is not None:
                asset_id = create_asset_id(driver)
                assert asset_id is not None
                driver_dict[asset_id] = driver
            else:
                asset_id = None
            if coin_spend.coin.parent_coin_info == bytes32.zeros:
                notarized_payments: list[NotarizedPayment] = []
                for payment_group in Program.from_serialized(coin_spend.solution).as_iter():
                    nonce = bytes32(payment_group.first().as_atom())
                    payment_args_list = payment_group.rest().as_iter()
                    notarized_payments.extend(
                        [NotarizedPayment.from_condition_and_nonce(condition, nonce) for condition in payment_args_list]
                    )

                requested_payments[asset_id] = notarized_payments
            else:
                leftover_coin_spends.append(coin_spend)

        return cls(
            requested_payments, WalletSpendBundle(leftover_coin_spends, bundle.aggregated_signature), driver_dict
        )
```

**File:** chia/cmds/wallet_funcs.py (L827-831)
```python
    try:
        offer = Offer.from_bech32(offer_hex)
    except ValueError:
        print("Please enter a valid offer file or hex blob")
        return []
```

**File:** chia/wallet/wallet_request_types.py (L2304-2312)
```python
@streamable
@dataclass(kw_only=True, frozen=True)
class TakeOffer(TransactionEndpointRequest):
    offer: str
    solver: Solver | None = None

    @cached_property
    def parsed_offer(self) -> Offer:
        return Offer.from_bech32(self.offer)
```

**File:** chia/wallet/wallet_rpc_api.py (L2042-2048)
```python
    async def check_offer_validity(self, request: CheckOfferValidity) -> CheckOfferValidityResponse:
        offer = Offer.from_bech32(request.offer)
        peer = self.service.get_full_node_peer()
        return CheckOfferValidityResponse(
            valid=(await self.service.wallet_state_manager.trade_manager.check_offer_validity(offer, peer)),
            id=offer.name(),
        )
```
