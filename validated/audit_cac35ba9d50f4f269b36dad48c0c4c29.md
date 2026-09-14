### Title
Unvalidated attacker-controlled metadata key in NFT `-24` update condition causes unhandled `KeyError` and halts wallet coin-state processing - (File: `chia/wallet/nft_wallet/nft_puzzle_utils.py`)

### Summary
The Kibana CVE-2020-7013 stems from an authenticated, lower-privileged actor supplying attacker-controlled data (a TSVB visualization payload) that is consumed by trusted logic without adequate validation, causing unexpected/unsafe execution. The closest reachable analog in this codebase is the NFT metadata-update path, where an on-chain `-24` (metadata update) condition — fully controlled by whoever creates the NFT coin spend — supplies an arbitrary "key" that is used directly to index into a Python `dict` without existence checking, in `prepend_value()`.

### Finding Description
When the wallet processes an NFT coin's parent spend (during sync, via `NFTWallet.puzzle_solution_received` / `get_metadata_and_phs`), it inspects conditions output by the NFT's inner (p2) puzzle. A condition with opcode `-24` is treated as a metadata update and dispatched to `update_metadata()`: [1](#0-0) 

`update_metadata()` extracts an arbitrary `uri` key/value pair straight from the CLVM condition arguments supplied in the coin spend's solution, with no validation that the key is one of the recognized metadata fields (`u`, `h`, `mu`, `mh`, `lu`, `lh`, `sn`, `st`): [2](#0-1) 

That attacker-chosen `key` is passed into `prepend_value()`, which indexes the metadata dict directly (`metadata[key]`) without checking whether the key exists: [3](#0-2) 

Because `nft_program_to_metadata()` only populates the dict with whatever key/value pairs were actually present in the on-chain metadata program (which itself can be arbitrary, since NFT metadata is attacker-supplied at mint time and only loosely structured by `UncurriedNFT.uncurry()`): [4](#0-3) 

...a spend that emits a `-24` condition referencing a key absent from the coin's current metadata (e.g., a metadata program that never included `mu`/`lu`/etc., followed by an update condition targeting one of those keys, or any novel key) causes an unhandled `KeyError` inside `metadata[key]`. This is directly analogous to the CVE-class bug: untrusted, attacker-chosen "property name" data reaches a raw dictionary/object write path without a whitelist or existence check.

### Impact Explanation
This code path executes during normal wallet sync whenever the wallet is tracking (owns or watches) an NFT coin and observes a new spend of it — this is reachable by any party who can construct/broadcast a spend of an NFT (the current owner, or via any spend that a watching wallet processes), not just the NFT's own wallet operator. An uncaught `KeyError` propagating out of `get_metadata_and_phs()` during `puzzle_solution_received()` would abort processing of that coin's inbound state update, which is a "spend-triggered transaction-processing halt" — potentially causing the wallet's NFT sync path to raise and disrupt state manager bookkeeping for that coin (and, depending on caller error-handling, could cascade to broader sync-loop failures for the affected wallet, a targeted denial-of-service against a specific NFT wallet/coin, not a remote-code-execution or unauthorized asset movement).

### Likelihood Explanation
High likelihood of reachability: constructing a spend of an owned NFT (or an NFT being tracked/offered) with a state-layer inner solution containing a `-24` condition with a mismatched/unexpected key is straightforward CLVM crafting requiring no special privilege beyond owning or being able to spend an NFT coin — a capability any wallet user or offer counterparty already has. The severity of the resulting failure (an uncaught exception vs. a caught/logged one) could not be fully confirmed from the available index; the exact behavior of the caller (`NFTWallet.puzzle_solution_received`, wallet sync dispatcher) around exception handling for this specific code path was not located in the searched context.

### Recommendation
In `prepend_value()`, validate that `key` exists in `metadata` (or use `metadata.get(key, b"")`) before indexing, and in `update_metadata()`/`get_metadata_and_phs()` reject or ignore `-24` conditions referencing unrecognized metadata keys instead of allowing a raw dict KeyError to propagate. Wrap NFT metadata-update condition processing in exception handling so a single malformed/malicious update condition cannot abort wallet-wide coin-state processing.

### Proof of Concept
1. Mint (or acquire) an NFT whose on-chain metadata program only contains keys `u` and `h` (standard minimal metadata).
2. Construct a spend of the NFT's p2/inner puzzle whose solution causes the puzzle to output a condition `(-24 updater_puzzle (KEY . VALUE))` where `KEY` is `"mu"` (or any key not already present in the metadata dict) and `VALUE` is non-nil.
3. Broadcast/confirm this spend so that a wallet tracking the NFT (owner's wallet, an offer counterparty's wallet, or any wallet subscribed to the coin) processes it via sync.
4. The wallet's `get_metadata_and_phs()` → `update_metadata()` → `prepend_value()` call chain executes `metadata[key]` where `key` (`"mu"`) is absent from the dict, raising an unhandled `KeyError` and halting processing of that coin's state update. [5](#0-4)

### Citations

**File:** chia/wallet/nft_wallet/nft_puzzle_utils.py (L140-149)
```python
def nft_program_to_metadata(program: Program) -> dict[bytes, Any]:
    """
    Convert a program to a metadata dict
    :param program: Chialisp program contains the metadata
    :return: Metadata dict
    """
    metadata = {}
    for kv_pair in program.as_iter():
        metadata[kv_pair.first().as_atom()] = kv_pair.rest().as_python()
    return metadata
```

**File:** chia/wallet/nft_wallet/nft_puzzle_utils.py (L152-177)
```python
def prepend_value(key: bytes, value: Program, metadata: dict[bytes, Any]) -> None:
    """
    Prepend a value to a list in the metadata
    :param key: Key of the field
    :param value: Value want to add
    :param metadata: Metadata
    :return:
    """
    if value != Program.to(0):
        if metadata[key] == b"":
            metadata[key] = [value.as_python()]
        else:
            metadata[key].insert(0, value.as_python())


def update_metadata(metadata: Program, update_condition: Program) -> Program:
    """
    Apply conditions of metadata updater to the previous metadata
    :param metadata: Previous metadata
    :param update_condition: Update metadata conditions
    :return: Updated metadata
    """
    new_metadata: dict[bytes, Any] = nft_program_to_metadata(metadata)
    uri: Program = update_condition.rest().rest().first()
    prepend_value(uri.first().as_python(), uri.rest(), new_metadata)
    return metadata_to_program(new_metadata)
```

**File:** chia/wallet/nft_wallet/nft_puzzle_utils.py (L234-247)
```python
def get_metadata_and_phs(unft: UncurriedNFT, solution: SerializedProgram) -> tuple[Program, bytes32]:
    conditions = unft.p2_puzzle.run(unft.get_innermost_solution(Program.from_serialized(solution)))
    metadata = unft.metadata
    puzhash_for_derivation: bytes32 | None = None
    for condition in conditions.as_iter():
        if condition.list_len() < 2:
            # invalid condition
            continue
        condition_code = condition.first().as_int()
        log.debug("Checking condition code: %r", condition_code)
        if condition_code == -24:
            # metadata update
            metadata = update_metadata(metadata, condition)
            metadata = Program.to(metadata)
```
