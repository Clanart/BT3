Based on my investigation, the strongest analog in this codebase is the unbounded precision-refinement loop in `ToShortestFloat` in `python/google/protobuf/internal/type_checkers.py`, which mirrors the ImageMagick `GetXmpNumeratorAndDenominator` pattern: an iterative loop that keeps refining a numeric representation until an equality test succeeds, with no iteration cap.

### Title
Unbounded iterative-refinement loop in Python `ToShortestFloat` mirrors ImageMagick's `GetXmpNumeratorAndDenominator` hang pattern - ([File: python/google/protobuf/internal/type_checkers.py])

### Summary
The ImageMagick advisory shows `GetXmpNumeratorAndDenominator` looping `while(fabs(df - value) > MagickEpsilon)`, incrementing a denominator/numerator pair until a floating-point equality-style condition is satisfied — a condition that can never converge for certain attacker-influenced `value`s, causing an infinite loop. The protobuf Python runtime has a structurally identical pattern in `ToShortestFloat`, which loops on `while TruncateToFourByteFloat(rounded) != original`, incrementing precision each iteration until round-tripping through a 4-byte float format matches the original value exactly [1](#0-0) .

### Finding Description
`ToShortestFloat(original)` starts at `precision = 6` and repeatedly formats/reparses `original` with increasing significant digits, checking termination via `TruncateToFourByteFloat(rounded) != original` [2](#0-1) . This is the exact failed invariant class as the ImageMagick bug: an iterative approximation loop whose termination depends on an equality test against a value that may never be reproducible bit-for-bit through the round-trip transform (`struct.pack`/`struct.unpack` via `TruncateToFourByteFloat`, at line 36-37) [3](#0-2) . For a value where `original` is `NaN`, `TruncateToFourByteFloat(rounded) != original` is always `True` in IEEE-754 semantics (`NaN != NaN`), so the loop's termination condition can never be satisfied, and `precision` grows without bound.

### Impact Explanation
If reachable with an attacker-controlled value (e.g., a `float`/`double` field carrying `NaN` serialized to JSON/TextFormat via a public parse-then-reformat API), this would cause the same class of impact as the ImageMagick advisory: an unbounded CPU-consuming loop (CWE-835) with no allocation growth, matching the "no unbounded-allocation but CPU hang" impact profile of the original report (`CVSS:AV:N/AC:L/A:H`).

### Likelihood Explanation
This claim carries real uncertainty I could not fully resolve within the available context. `ToShortestFloat` is referenced from `python/google/protobuf/json_format.py` and `python/google/protobuf/text_format.py`, but I was not able to confirm from the retrieved snippets whether those call sites special-case `NaN`/`Infinity` (both files contain multiple `isnan`/`isinf`/`_INFINITY`/`_NAN` references, e.g. `_INFINITY`, `_NEG_INFINITY`, `_NAN` constants defined in `json_format.py`) and short-circuit before reaching `ToShortestFloat`, which would make the analog non-reachable in practice for JSON output [4](#0-3) . Without confirming the exact call site and guard logic (which requires reading the specific `_FloatToJson`/text-format float-printing function bodies, not fully available in this index), I cannot assert the loop is actually attacker-reachable with an unguarded `NaN`/`Infinity` value.

### Recommendation
Given the confirmed unresolved uncertainty, this should be verified with a full read of the call sites in `json_format.py` and `text_format.py` around `ToShortestFloat` to check for pre-existing `isnan`/`isinf` guards. If such guards exist for all call paths, this analog does not transfer and should be dropped. If any call path passes an unguarded `NaN` (or a subnormal/edge-case float that similarly can't round-trip) into `ToShortestFloat`, add an explicit `isnan`/`isinf` check (and a maximum precision bound, e.g. capping at `DBL_DIG`/`FLT_DIG`+small margin) before or inside the loop, mirroring the fix pattern used for the ImageMagick issue.

### Proof of Concept
I was unable to run code in this environment to confirm actual reachability. The theoretical repro construction is: call `google.protobuf.internal.type_checkers.ToShortestFloat(float('nan'))` directly — this call is expected to loop indefinitely because `TruncateToFourByteFloat(rounded) != original` is always `True` for NaN inputs, per the code at [2](#0-1) . Whether this function is reachable with an unguarded NaN value from `MessageToJson`/`MessageToDict`-style public serialization APIs is **not confirmed** and requires further investigation of `json_format.py`'s float-handling function before treating this as a proven, exploitable finding.

### Citations

**File:** python/google/protobuf/internal/type_checkers.py (L36-37)
```python
def TruncateToFourByteFloat(original):
  return struct.unpack('<f', struct.pack('<f', original))[0]
```

**File:** python/google/protobuf/internal/type_checkers.py (L40-52)
```python
def ToShortestFloat(original):
  """Returns the shortest float that has same value in wire."""
  # All 4 byte floats have between 6 and 9 significant digits, so we
  # start with 6 as the lower bound.
  # It has to be iterative because use '.9g' directly can not get rid
  # of the noises for most values. For example if set a float_field=0.9
  # use '.9g' will print 0.899999976.
  precision = 6
  rounded = float('{0:.{1}g}'.format(original, precision))
  while TruncateToFourByteFloat(rounded) != original:
    precision += 1
    rounded = float('{0:.{1}g}'.format(original, precision))
  return rounded
```

**File:** python/google/protobuf/json_format.py (L48-50)
```python
_INFINITY = 'Infinity'
_NEG_INFINITY = '-Infinity'
_NAN = 'NaN'
```
