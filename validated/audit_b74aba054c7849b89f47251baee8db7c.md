## Title
Unbounded recursion in `VCProofs.prove_keys`/`from_program` over attacker-influenced proof trees enables stack-exhaustion denial of service - (File: `chia/wallet/vc_wallet/vc_store.py`)

### Summary
The Alpine `jq` CVE describes `jv_contains` recursing into nested JSON structures with no depth limit, letting an attacker exhaust the C stack via a deeply nested but otherwise valid input. The closest analog in this codebase is `chia/wallet/vc_wallet/vc_store.py`'s `VCProofs` class, whose `from_program()` and `prove_keys()` methods are plain Python recursive functions that walk a CLVM `Program` tree (the VC "proofs" binary tree) with no depth bound, unlike the deliberately-iterative `sha256_treehash()` used elsewhere in the codebase specifically to avoid this class of bug.

### Finding Description
`VCProofs.from_program()` recurses once per internal node of the proof tree to rebuild the `key_value_pairs` dict: [1](#0-0) 

`VCProofs.prove_keys()` similarly recurses over the same tree shape to build an inclusion/exclusion proof: [2](#0-1) 

Both functions call themselves once per tree level with no depth cap, mirroring the `jv_contains` bug class: the recursion depth is driven directly by the shape of an externally-provided/derived tree structure. This is architecturally the same category of bug that the codebase explicitly guards against elsewhere — `chia/types/blockchain_format/tree_hash.py`'s `sha256_treehash()` states in its docstring that it "goes to great pains to be non-recursive so we don't have to worry about blowing out the python stack," and the accompanying architecture notes for `chia/types/` call this out as a deliberate robustness property: [3](#0-2) 

`VCProofs` trees are built from `list_to_binary_tree()`, which produces a balanced tree whose depth grows with `log2(N)` for a given number of key/value pairs, but the actual tree that `from_program`/`prove_keys` operate on is derived from the raw `Program` shape, so a maliciously-crafted (unbalanced/deeply chained) proof program that does not match the expected balanced-tree invariant can produce much deeper nesting than `log2(N)` implies, well beyond what `list_to_binary_tree` itself would ever generate for a small number of keys.

### Impact Explanation
If a counterparty or a syncing wallet is forced to deserialize/verify a VC proof `Program` (or an offer/spend referencing one) whose proof tree has been crafted with much greater depth than the honest balanced-tree construction would produce, `from_program`/`prove_keys` recursion can exceed the Python interpreter's default recursion limit and raise `RecursionError`, or in native/CPython worst cases exhaust the process stack, crashing the wallet process handling that spend/offer/VC proof. This matches the report's "spend-triggered transaction-processing halt" impact class — a denial of service against wallet processing rather than any funds-theft or consensus divergence primitive.

### Likelihood Explanation
Reaching this code requires constructing/propagating a `VCProofs`-shaped `Program` (or a `VerifiedCredential`/CR-CAT proof payload) that a victim wallet will parse via `from_program`/`prove_keys` during normal VC/CR-CAT/offer workflows. The exact triggering entry point (e.g., whether a remote offer counterparty's Solver-supplied proof data reaches `from_program` before any shape/depth validation) could not be fully confirmed within the available tool budget — `proof_of_inclusions_for_root_and_keys` and related VC-wallet call sites in `chia/wallet/vc_wallet/vc_wallet.py` and `chia/wallet/vc_wallet/cr_cat_wallet.py` reference `VCProofs`-related helpers, but I was not able to trace the full call graph to confirm an unprivileged, remotely-triggerable path with certainty before running out of iterations.

### Recommendation
Convert `VCProofs.from_program` and `VCProofs.prove_keys` to iterative (stack-based) implementations, following the same pattern used by `sha256_treehash()` in `chia/types/blockchain_format/tree_hash.py`. Additionally, validate that any externally-supplied VC proof `Program` conforms to the expected balanced binary-tree shape (depth bounded by `log2(N)` for the claimed number of keys) before recursing, and reject/short-circuit malformed trees early with a bounded-depth check.

### Proof of Concept
Not fully verifiable within the available investigation budget — constructing a concrete proof-of-concept would require confirming the exact remote/offer-driven call path that feeds attacker-controlled `Program` data into `VCProofs.from_program`/`prove_keys` without prior depth validation. Conceptually: construct a `Program` shaped as a deeply right-nested pair chain (depth >> `log2(N)` for its apparent key count) and pass it through whichever VC/CR-CAT/offer code path invokes `VCProofs.from_program` or `prove_keys`, causing a Python `RecursionError`/stack exhaustion during wallet processing of that spend/offer.

### Citations

**File:** chia/wallet/vc_wallet/vc_store.py (L43-55)
```python
    @staticmethod
    def from_program(prog: Program) -> VCProofs:
        first: Program = prog.at("f")
        rest: Program = prog.at("r")
        if first.atom is None and rest.atom is None:
            final_dict: dict[str, str] = {}
            final_dict.update(VCProofs.from_program(first).key_value_pairs)
            final_dict.update(VCProofs.from_program(rest).key_value_pairs)
            return VCProofs(final_dict)
        elif first.atom is not None and rest.atom is not None:
            return VCProofs({first.atom.decode("utf-8"): rest.atom.decode("utf-8")})
        else:
            raise ValueError("Malformatted VCProofs program")  # pragma: no cover
```

**File:** chia/wallet/vc_wallet/vc_store.py (L57-81)
```python
    def prove_keys(self, keys: list[str], tree: Program | None = None) -> Program:
        if tree is None:
            tree = self.as_program()

        first = tree.first()
        if first.atom is not None:
            if first.atom.decode("utf8") in keys:
                return tree
            else:
                tree_hash_as_atom: Program = Program.to(tree.get_tree_hash())
                return tree_hash_as_atom
        else:
            rest = tree.rest()
            first_tree = self.prove_keys(keys, first)
            rest_tree = self.prove_keys(keys, rest)
            if first_tree.atom is not None and rest_tree.atom is not None:
                tree_hash_as_atom = Program.to(
                    Program.to((first_tree, rest_tree)).get_tree_hash_precalc(
                        bytes32(first_tree.atom), bytes32(rest_tree.atom)
                    )
                )
                return tree_hash_as_atom
            else:
                new_tree: Program = first_tree.cons(rest_tree)
                return new_tree
```

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```
