Based on the investigation, I found a genuine, unfixed analog. The Kibana Timelion CVE is CWE-400 (Uncontrolled Resource Consumption via Input Data Manipulation) — a case where an attacker-controlled value drives disproportionate CPU work per byte of input. The codebase already fixed the exact same bug class in Java's ProtoJSON parser but the equivalent Python code path lacks the same guard.

### Title
Unbounded CPU consumption via oversized numeric strings in Python ProtoJSON `_ConvertInteger`/`_ConvertFloat` - (File: `python/google/protobuf/json_format.py`)

### Summary
Python's ProtoJSON parser (`json_format.Parse`) converts attacker-controlled JSON scalar values for integer/float fields by calling Python's built-in `int(value)` / `float(value)` directly on the untrusted string, with no length bound on the numeric string before conversion [1](#0-0) . This is the same CWE-400 bug class that Java's `JsonFormat` explicitly patched: `BigDecimal(String)` construction on untrusted numeric strings has superlinear (O(N²) on affected JDKs) time complexity, so Java added `MAX_NUMERIC_STRING_LENGTH = 1000` specifically to reject long numeric strings before expensive parsing [2](#0-1) , with an explicit regression test asserting rejection of a 10,000-digit string "to prevent O(N^2) BigDecimal parsing DoS" [3](#0-2) . The Python `_ConvertInteger`/`_ConvertFloat` code path has no equivalent length check before invoking `int()`/`float()` on the attacker-supplied string [4](#0-3) .

### Finding Description
The external report describes a component (Timelion) accepting attacker input that drives disproportionate CPU work relative to input size — a missing bound on an expensive conversion operation applied to attacker-controlled data. The transferable invariant is: "any numeric-string-to-number conversion reachable from an untrusted parse API must bound the string length before invoking a superlinear conversion routine."

In `python/google/protobuf/json_format.py`, `_ConvertScalarFieldValue` dispatches string values for integer and floating-point fields to `_ConvertInteger(value)` and `_ConvertFloat(value, field)` [5](#0-4) . `_ConvertInteger` calls `int(value)` directly on the attacker string, and on failure falls back to `float(value)` [6](#0-5) . Both `int(str)` and, to a lesser extent, decimal-string parsing routines exhibit quadratic-time behavior for very long digit strings in CPython prior to 3.11 (this exact behavior was the basis of CVE-2020-10735, fixed upstream in CPython 3.11+ by adding a configurable `sys.set_int_max_str_digits` default limit of 4300 digits — a runtime-level mitigation outside protobuf's control, and one that can be explicitly disabled by application code via `sys.set_int_max_str_digits(0)`). Unlike Java's fix, protobuf's Python implementation contains no length gate of its own before calling `int()`/`float()`, so on Python interpreters or configurations where the built-in digit-limit protection is absent or disabled, a single ProtoJSON message containing one long numeric string field value drives the vulnerable conversion path with no defense-in-depth from protobuf itself.

### Impact Explanation
A consuming application that calls `google.protobuf.json_format.Parse`/`MessageToDict` equivalents on untrusted ProtoJSON (e.g., a network-facing service deserializing client-supplied protobuf-JSON payloads) can be driven into disproportionate CPU consumption by a single request containing one scalar integer/float field whose JSON value is an unusually long digit string. This matches the "Uncontrolled Resource Consumption... via Input Data Manipulation" pattern of the external report: a bounded, ordinary-looking payload (one field, one string) causing CPU work disproportionate to its size, rather than raw memory/size flooding.

### Likelihood Explanation
Exploitability depends on the runtime protecting against long integer-string conversion (CPython 3.11+ default). Protobuf's Python `json_format.py` itself provides no such guard, meaning the affected surface is real for any deployment on Python <3.11, or where `sys.set_int_max_str_digits(0)` has been set (a documented compatibility escape hatch some applications use). Given protobuf explicitly hardened the semantically identical code path in Java with a hardcoded constant and regression test, the omission in Python represents an inconsistency in the project's own established mitigation for this bug class, making it Medium/Low likelihood contingent on runtime version — not the strongest Critical/High case, but a legitimate, provable gap.

### Recommendation
Add an explicit length guard in `_ConvertInteger`/`_ConvertFloat` (or the shared string-scalar conversion path) in `python/google/protobuf/json_format.py`, analogous to Java's `MAX_NUMERIC_STRING_LENGTH`/`parseBigDecimal` guard, rejecting numeric strings beyond a generous bound (e.g., a few hundred characters, consistent with the longest legitimate int64/double textual representation) before calling `int()`/`float()`.

### Proof of Concept
```python
from google.protobuf import json_format
from google.protobuf import struct_pb2  # or any message with an int64/double field

msg = SomeMessageWithInt64Field()
huge_digits = "9" * 2_000_000  # single JSON field, no huge overall payload needed on vulnerable interpreters
json_format.Parse('{"someInt64Field": "%s"}' % huge_digits, msg)
```
On a Python interpreter/configuration without the CPython 3.11+ integer-string digit-limit protection active, this call drives `int(value)` in `_ConvertInteger` [6](#0-5)  into quadratic-time work for a single, unremarkable-looking JSON message, with no protobuf-side length check to short-circuit it — the same class of issue Java's `JsonFormat` proactively defends against [7](#0-6) .

### Citations

**File:** python/google/protobuf/json_format.py (L1050-1054)
```python
  try:
    if field.cpp_type in _INT_TYPES:
      return _ConvertInteger(value)
    elif field.cpp_type in _FLOAT_TYPES:
      return _ConvertFloat(value, field)
```

**File:** python/google/protobuf/json_format.py (L1116-1152)
```python
def _ConvertInteger(value):
  """Convert an integer.

  Args:
    value: A scalar value to convert.

  Returns:
    The integer value.

  Raises:
    ParseError: If an integer couldn't be consumed.
  """
  if isinstance(value, float) and not value.is_integer():
    raise ParseError("Couldn't parse integer: {0}".format(value))

  if isinstance(value, str) and value.find(' ') != -1:
    raise ParseError('Couldn\'t parse integer: "{0}"'.format(value))

  if isinstance(value, bool):
    raise ParseError(
        'Bool value {0} is not acceptable for integer field'.format(value)
    )

  try:
    return int(value)
  except ValueError as e:
    # Attempt to parse as an integer-valued float.
    try:
      f = float(value)
    except ValueError:
      # Raise the original exception for the int parse.
      raise e  # pylint: disable=raise-missing-from
    if not f.is_integer():
      raise ParseError(
          'Couldn\'t parse non-integer string: "{0}"'.format(value)
      ) from e
    return int(f)
```

**File:** python/google/protobuf/json_format.py (L1155-1192)
```python
def _ConvertFloat(value, field):
  """Convert an floating point number."""
  if isinstance(value, float):
    if math.isnan(value):
      raise ParseError('Couldn\'t parse NaN, use quoted "NaN" instead')
    if math.isinf(value):
      if value > 0:
        raise ParseError(
            "Couldn't parse Infinity or value too large, "
            'use quoted "Infinity" instead'
        )
      else:
        raise ParseError(
            "Couldn't parse -Infinity or value too small, "
            'use quoted "-Infinity" instead'
        )
    if field.cpp_type == descriptor.FieldDescriptor.CPPTYPE_FLOAT:
      # pylint: disable=protected-access
      if value > type_checkers._FLOAT_MAX:
        raise ParseError('Float value too large')
      # pylint: disable=protected-access
      if value < type_checkers._FLOAT_MIN:
        raise ParseError('Float value too small')
  if value == 'nan':
    raise ParseError('Couldn\'t parse float "nan", use "NaN" instead')
  try:
    # Assume Python compatible syntax.
    return float(value)
  except ValueError as e:
    # Check alternative spellings.
    if value == _NEG_INFINITY:
      return float('-inf')
    elif value == _INFINITY:
      return float('inf')
    elif value == _NAN:
      return float('nan')
    else:
      raise ParseError("Couldn't parse float: {0}".format(value)) from e
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L2305-2321)
```java
    private static final BigDecimal MAX_UINT32 = new BigDecimal(0xFFFFFFFFL);
    private static final BigDecimal MAX_UINT64 =
        new BigDecimal(new BigInteger("FFFFFFFFFFFFFFFF", 16));

    // Maximum length for numeric strings passed to BigDecimal constructor.
    // BigDecimal(String) has O(N^2) time complexity for N-digit strings on JDK < 18,
    // allowing a DoS with a single long numeric JSON value. Valid protobuf numeric
    // values never exceed ~350 characters, so 1000 is a generous upper bound.
    private static final int MAX_NUMERIC_STRING_LENGTH = 1000;

    private static BigDecimal parseBigDecimal(String value) throws InvalidProtocolBufferException {
      if (value.length() > MAX_NUMERIC_STRING_LENGTH) {
        throw new InvalidProtocolBufferException(
            "Numeric value is too long: " + value.length() + " characters");
      }
      return new BigDecimal(value);
    }
```

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L800-821)
```java
  @Test
  public void testParserRejectOverlyLongNumericStrings() throws Exception {
    // A numeric string with 10,000 digits should be rejected quickly to prevent
    // O(N^2) BigDecimal parsing DoS.
    StringBuilder sb = new StringBuilder("1");
    for (int i = 0; i < 10000; i++) {
      sb.append('0');
    }
    String longNumber = sb.toString();
    String[] fields = {
      "optionalInt32", "optionalInt64", "optionalUint32", "optionalUint64", "optionalDouble"
    };
    for (String field : fields) {
      TestAllTypes.Builder builder = TestAllTypes.newBuilder();
      try {
        mergeFromJson("{\"" + field + "\":\"" + longNumber + "\"}", builder);
        assertWithMessage("Exception expected for " + field + " with long numeric string").fail();
      } catch (InvalidProtocolBufferException expected) {
        // Expected: rejected before expensive BigDecimal construction.
      }
    }
  }
```
