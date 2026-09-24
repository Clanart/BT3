### Title
Infinite loop when serializing NaN float fields via `ToShortestFloat` - ([File: python/google/protobuf/internal/type_checkers.py])

### Summary
The CVE-2017-7619 pattern is a `while` loop whose termination depends on a floating-point equality/round-trip check that never becomes true for certain inputs, causing an unbounded CPU-bound loop (denial of service). The closest transferable analog in this Protobuf checkout is `ToShortestFloat()` in the Python implementation, which loops on a round-trip float comparison to find the "shortest" textual representation of a `float` field value during JSON/text serialization.

### Finding Description
`ToShortestFloat` uses an unbounded `while` loop keyed on floating-point equality: [1](#0-0) 

The loop's exit condition is `TruncateToFourByteFloat(rounded) != original`. For a normal finite float this loop terminates once enough decimal digits are printed and the round-tripped value matches. For `original = float('nan')`, however, `nan != nan` is always `True` in Python floating-point semantics, and formatting NaN with increasing precision (`'{0:.{N}g}'.format(nan, precision)`) always yields the literal string `'nan'` regardless of precision. Consequently `rounded` is always `nan`, `TruncateToFourByteFloat(rounded)` is always `nan`, and the inequality `nan != nan` never becomes `False` — the loop never terminates, incrementing `precision` indefinitely while burning CPU.

This mirrors the ImageMagick invariant failure: a per-iteration floating-point comparison used as a loop-termination check that is not guaranteed to converge for a class of values (there, colors after HSL/HSB modulation rounding; here, NaN payloads after round-trip truncation).

The attacker-controlled value is the wire-encoded `float`/`double` field payload. Binary protobuf places no restriction on the bit pattern of a fixed32/fixed64 field decoded as `float`/`double` — any 4/8-byte pattern, including any NaN encoding, is a legal, ordinary client-supplied value accepted by `ParseFromString`. No prior sanitization rejects NaN in the parse path; the only place a NaN would matter is downstream, when the application serializes the parsed message back out via JSON (`json_format.MessageToJson`) or `TextFormat` printing (`str(message)`), both of which are public, supported protobuf output APIs.

### Impact Explanation
If a consuming application accepts an untrusted binary protobuf payload with a `float` (not `double`) field set to a NaN bit pattern, and later serializes that message to JSON or text (a common pattern for logging, debugging, or re-emitting received messages), the pure-Python `ToShortestFloat` path would hang indefinitely on a single thread, consuming 100% CPU with no way to escape short of external termination. This is a classic algorithmic-complexity/availability bug, analogous in class (not in domain) to CVE-2017-7619.

### Likelihood Explanation
This is contingent on: (1) the pure-Python implementation being in use rather than the C++/upb backend (the C/upb equivalents `_upb_EncodeRoundTripFloat`/`SimpleFtoa` explicitly special-case `isnan(val)` and short-circuit before entering any retry loop, so they are not vulnerable — see below), and (2) the application actually re-serializing an attacker-supplied `float` field to JSON/text after parsing. I was not able to fully confirm, within the remaining investigation budget, whether `ToShortestFloat` is reached with a raw NaN value unconditionally or whether `json_format.py`/`text_format.py` special-case NaN before calling it — the grep for `isnan`/`ToShortestFloat` in `json_format.py` returned matches I did not get to inspect in detail before the iteration budget ran out. This should be verified directly (see PoC/verification steps below) before treating this as confirmed.

By contrast, the C++ (`src/google/protobuf/io/strtod.cc`) and upb (`upb/lex/round_trip.c`, and its copies in `php/ext/google/protobuf/php-upb.c`, `ruby/ext/google/protobuf_c/ruby-upb.c`) round-trip encoders all check `isnan(val)` first and return immediately, and their "retry" logic is a single conditional re-print (not a loop), so they cannot loop indefinitely: [2](#0-1) [3](#0-2) 

This confirms the pattern is specific to the pure-Python `ToShortestFloat` helper, which lacks the equivalent `isnan` guard that its C++/upb counterparts have.

### Recommendation
Add an explicit `math.isnan(original)` (and `math.isinf`) check at the top of `ToShortestFloat` in `python/google/protobuf/internal/type_checkers.py`, mirroring the guard already present in `_upb_EncodeRoundTripFloat`/`SimpleFtoa`, returning immediately for non-finite values instead of entering the round-trip loop. Additionally, bound the loop with a maximum precision (e.g., 9, since that's documented as the max needed for 4-byte floats) to make the function robust against any other unforeseen non-convergent case.

### Proof of Concept
Conceptual reproduction (pure Python, no network/access to a shell required to reason about it, but should be executed to confirm):
```python
from google.protobuf.internal import type_checkers
import math
nan = float('nan')
# This call is expected to hang indefinitely:
type_checkers.ToShortestFloat(nan)
```
To confirm the full attacker-reachable chain, a Devin session with terminal access should:
1. Build a minimal `.proto` with a `float` field, generate Python bindings.
2. Craft raw wire bytes encoding a NaN bit pattern for that field (fixed32 tag + NaN bytes) and call `ParseFromString` on the pure-Python implementation.
3. Call `google.protobuf.json_format.MessageToJson(msg)` and/or `str(msg)`/`text_format.MessageToString(msg)` on the parsed message and observe whether the process hangs, confirming whether `ToShortestFloat` (or a NaN-guard preceding it) is actually reached with this value. [4](#0-3)

### Citations

**File:** python/google/protobuf/internal/type_checkers.py (L36-52)
```python
def TruncateToFourByteFloat(original):
  return struct.unpack('<f', struct.pack('<f', original))[0]


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

**File:** upb/lex/round_trip.c (L31-43)
```c
void _upb_EncodeRoundTripDouble(double val, char* buf, size_t size) {
  assert(size >= kUpb_RoundTripBufferSize);
  if (isnan(val)) {
    snprintf(buf, size, "%s", "nan");
    return;
  }
  snprintf(buf, size, "%.*g", DBL_DIG, val);
  if (strtod(buf, NULL) != val) {
    snprintf(buf, size, "%.*g", DBL_DIG + 2, val);
    assert(strtod(buf, NULL) == val);
  }
  upb_FixLocale(buf);
}
```

**File:** src/google/protobuf/io/strtod.cc (L203-219)
```text
char *DoubleToBuffer(double value, char *buffer) {
  // DBL_DIG is 15 for IEEE-754 doubles, which are used on almost all
  // platforms these days.  Just in case some system exists where DBL_DIG
  // is significantly larger -- and risks overflowing our buffer -- we have
  // this assert.
  static_assert(DBL_DIG < 20, "DBL_DIG_is_too_big");

  if (value == std::numeric_limits<double>::infinity()) {
    absl::SNPrintF(buffer, kDoubleToBufferSize, "inf");
    return buffer;
  } else if (value == -std::numeric_limits<double>::infinity()) {
    absl::SNPrintF(buffer, kDoubleToBufferSize, "-inf");
    return buffer;
  } else if (std::isnan(value)) {
    absl::SNPrintF(buffer, kDoubleToBufferSize, "nan");
    return buffer;
  }
```
